#!/usr/bin/python

import os, sys
import ast
import buderus
import cherrypy
import datetime
import json
import optparse
import re
import socket
import time

import helpers

class Root():
    favicon_ico = None

class API():
    class Data():
        def __init__(self, api):
            self.api = api
            self.GET = helpers.notImplemented
            self.PUT = helpers.notImplemented
            self.DELETE = helpers.notImplemented

        @cherrypy.tools.json_in()
        @cherrypy.tools.json_out(handler=helpers.dumper)
        def POST(self):
            #(user, readonly) = helpers.authorisedUser()
            #if not user:
            #    raise cherrypy.HTTPError(403)

            criteria = cherrypy.request.json

            if 'date' in criteria:
                m = re.match('\A(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\Z', criteria['date'])
                if not m:
                    raise cherrypy.HTTPError(400)
                date = datetime.datetime(year=m.group('year'), month=m.group('month'), day=m.group('day'))
            else:
                date = datetime.datetime.now()

            if 'dayKeys' in criteria and not isinstance(criteria['dayKeys'], list):
                raise cherrypy.HTTPError(400)

            if 'yearKeys' in criteria and not isinstance(criteria['yearKeys'], list):
                raise cherrypy.HTTPError(400)

            skip = 1
            ua = cherrypy.request.headers.get('User-Agent', '').strip()
            if 'BlackBerry' in ua:
                skip = 4

            rc = {}
            with helpers.DatabaseCursor() as cursor:
                if 'dayKeys' in criteria:
                    cursor.execute('SELECT timestamp, name, value FROM buderusTimestamps, buderusKeys, buderusData WHERE DATE(timestamp) = DATE(%s) AND name IN %s AND buderusTimestamps.id = buderusData.timestampId AND buderusKeys.id = buderusData.keyId ORDER BY timestamp ASC', (date, criteria['dayKeys']))
                    for row in cursor:
                        if row['name'] not in rc:
                            rc[row['name']] = []
                        rc[row['name']].append([row['timestamp'], API.formatValue(row['value'])])

                if 'yearKeys' in criteria:
                    cursor.execute('SELECT DATE(timestamp) AS timestamp, name, MIN(value) AS min, MAX(value) AS max FROM buderusTimestamps, buderusKeys, buderusData WHERE YEAR(timestamp) = YEAR(%s) AND name IN %s AND buderusTimestamps.id = buderusData.timestampId AND buderusKeys.id = buderusData.keyId GROUP BY name, DATE(timestamp) ORDER BY name, DATE(timestamp) ASC', (date, criteria['yearKeys']))
                    lastValue = None
                    for row in cursor:
                        minKey = '%s.min' % row['name']
                        maxKey = '%s.max' % row['name']
                        deltaKey = '%s.delta' % row['name']
                        for k in [minKey, maxKey, deltaKey]:
                            if k not in rc:
                                rc[k] = []
                        minValue = API.formatValue(row['min'])
                        maxValue = API.formatValue(row['max'])
                        rc[minKey].append([row['timestamp'], minValue])
                        rc[maxKey].append([row['timestamp'], maxValue])
                        if lastValue:
                            rc[deltaKey].append([row['timestamp'], maxValue-lastValue])
                        lastValue = maxValue

            if skip != 1:
                for series in rc.keys():
                    newSeries = []
                    count = 0
                    for dataPoint in rc[series]:
                        if count % skip == 0:
                            newSeries.append(dataPoint)
                        count += 1
                    rc[series] = newSeries
            return rc

    def __init__(self):
        self.data = self.Data(self)
        self.data.exposed = True

        self.buderus = buderus.Buderus(host=cherrypy.config.get('buderus.host'), userPassword=cherrypy.config.get('buderus.userPassword'), gatewayPassword=cherrypy.config.get('buderus.gatewayPassword'))

    @staticmethod
    def formatValue(value):
        if value == 'off':
            return 0
        elif value == 'on':
            return 100
        else:
            try:
                return ast.literal_eval(value)
            except:
                return value

    @staticmethod
    def databaseParameters():
        return {'parameters': {'user':    cherrypy.config['database.user'],
                               'passwd':  cherrypy.config['database.password'],
                               'db':      cherrypy.config['database.name'],
                               'host':    cherrypy.config['database.host'],
                               'charset': cherrypy.config['database.charset']}}

    @staticmethod
    def assignDatabaseParameters(threadIndex):
        cherrypy.thread_data.db = API.databaseParameters()

    def queryBoiler(self):
        nodes = [ '/system/sensors/temperatures/outdoor_t1',
                  '/system/sensors/temperatures/supply_t1',
                  '/system/sensors/temperatures/return',
                  '/system/sensors/temperatures/hotWater_t2',
                  '/system/sensors/temperatures/switch',

                  '/heatSources/actualPower',
                  '/heatSources/actualCHPower',
                  '/heatSources/actualDHWPower',
                  '/heatSources/actualModulation',
                  '/heatSources/flameStatus',
                  '/heatSources/CHpumpModulation',
                  '/heatSources/systemPressure',
                  '/heatSources/numberOfStarts',

                  '/heatSources/workingTime/totalSystem',
                  '/heatSources/workingTime/secondBurner',
                  '/heatSources/workingTime/centralHeating',

                  '/heatingCircuits/hc1/actualSupplyTemperature',
                  '/heatingCircuits/hc1/operationMode',
                  '/heatingCircuits/hc1/temperatureLevels/eco',
                  '/heatingCircuits/hc1/temperatureLevels/comfort2',
                  '/heatingCircuits/hc1/temporaryRoomSetpoint',
                  '/heatingCircuits/hc1/roomtemperature',
                  '/heatingCircuits/hc1/pumpModulation',
                  '/heatingCircuits/hc1/fastHeatupFactor',

                  '/dhwCircuits/dhw1/actualTemp',
                  '/dhwCircuits/dhw1/waterFlow',
                  '/dhwCircuits/dhw1/workingTime',
                  '/dhwCircuits/dhw1/temperatureLevels/high',

                  '/solarCircuits/sc1/dhwTankTemperature',
                  '/solarCircuits/sc1/solarYield',
                  '/solarCircuits/sc1/pumpModulation',
                  '/solarCircuits/sc1/collectorTemperature',
                  '/solarCircuits/sc1/actuatorStatus',
                ]

        with helpers.DatabaseCursor(maxErrors=None) as cursor:
            cursor.execute('INSERT INTO buderusTimestamps () VALUES()')
            timestampId = cursor.lastrowid()

            cursor.execute('SELECT * FROM buderusKeys')
            keyNames = cursor.fetchall()
            for node in nodes:
                jsonText = self.buderus.get_data(node)
                if not jsonText:
                    continue
                data = json.loads(jsonText)
                data['id'] = data['id'].lstrip('/').replace('/', '.')

                keyName = filter(lambda k: k['name'] == data['id'], keyNames)
                if len(keyName) == 0:
                    cursor.execute('INSERT INTO buderusKeys (name, unit) VALUES(%s, %s)', (data['id'], data.get('unitOfMeasure')))
                    keyId = cursor.lastrowid()
                else:
                    keyId = keyName[0]['id']

                cursor.execute('INSERT INTO buderusData (timestampId, keyId, value) VALUES(%s, %s, %s)', (timestampId, keyId, data['value']))

def main():
    parser = optparse.OptionParser(usage='usage: %s' % os.path.basename(__file__))

    parser.add_option('--foreground', action='store_true', help='Don\'t daemonize')
    parser.add_option('--config', default=os.path.join(os.path.dirname(__file__), 'config.ini'), help='Path to config.ini')
    (opts, args) = parser.parse_args()

    if not opts.config:
        print 'config.ini file not specified'
        return 1

    if not opts.foreground:
        cherrypy.process.plugins.Daemonizer(cherrypy.engine).subscribe()

    os.chdir(os.path.dirname(__file__))
    cherrypy.config.update(opts.config)
    for log in ['access_file', 'error_file', 'pid_file']:
        path = cherrypy.config.get('log.%s' % log)
        if not path.startswith('/'):
            cherrypy.config.update({'log.%s' % log: os.path.join(os.path.abspath(os.path.dirname(__file__)), path)})

    if cherrypy.config.get('syslog.server'):
        h = logging.handlers.SysLogHandler(address=(cherrypy.config['syslog.server'], socket.getservbyname('syslog', 'udp')))
        h.setLevel(logging.INFO)
        h.setFormatter(cherrypy._cplogging.logfmt)
        cherrypy.log.access_log.addHandler(h)

    if cherrypy.config.get('log.pid_file'):
        cherrypy.process.plugins.PIDFile(cherrypy.engine, cherrypy.config.get('log.pid_file')).subscribe()

    rootConfig = {'/': {'tools.staticdir.on': True,
                         'tools.staticdir.root': os.path.dirname(os.path.abspath(__file__)),
                         'tools.staticdir.dir': 'static',
                         'tools.staticdir.index': 'index.html',
                         'tools.gzip.mime_types': ['text/*', 'application/*'],
                         'tools.gzip.on': True,
                         'tools.proxy.on': True}}
    apiConfig  = {'/': {'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
                         'tools.gzip.mime_types': ['text/*', 'application/*'],
                         'tools.gzip.on': True,
                         'tools.proxy.on': True}}

    api = API()
    cherrypy.tree.mount(Root(), '/', config=rootConfig)
    cherrypy.tree.mount(api, '/api/buderus/1.0', config=apiConfig)

    cherrypy.engine.subscribe('start_thread', API.assignDatabaseParameters)

    cherrypy.engine.start()
    helpers.StubbornDBBackgroundTask(API.databaseParameters, 60, api.queryBoiler).start()
    cherrypy.engine.block()

if __name__ == '__main__':
    sys.exit(main())

