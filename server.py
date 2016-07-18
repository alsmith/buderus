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

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import helpers

class Root():
    favicon_ico = None

class API():
    class Data():
        def __init__(self, api):
            self.api = api
            self.POST = helpers.notImplemented
            self.PUT = helpers.notImplemented
            self.DELETE = helpers.notImplemented

        @cherrypy.tools.json_out(handler=helpers.dumper)
        def GET(self, **kwargs):
            if 'date' in kwargs:
                m = re.match('\A(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\Z', kwargs['date'])
                if not m:
                    raise cherrypy.HTTPError(400)
                date = "'%s'" % kwargs['date']
            else:
                date = 'CURRENT_TIMESTAMP()'

            skip = 1
            ua = cherrypy.request.headers.get('User-Agent', '').strip()
            if 'BlackBerry' in ua:
                skip = 4

            with helpers.DatabaseCursor() as cursor:
                cursor.execute('SELECT DISTINCT nameId, name FROM buderusData, buderusKeys WHERE DATE(timestamp) = DATE(%s) AND buderusKeys.id = buderusData.nameId' % date)
                nodes = cursor.fetchall()
                rc = {}
                for node in nodes:
                    rc[node['name']] = []
                    cursor.execute('SELECT timestamp,value FROM buderusData WHERE DATE(timestamp) = DATE(%s) AND nameId = %%s ORDER BY TIMESTAMP ASC' % date, (node['nameId'],))
                    count = 0
                    for row in cursor:
                        if row['value'] == 'off':
                            value = 0
                        elif row['value'] == 'on':
                            value = 100
                        else:
                            try:
                                value = ast.literal_eval(row['value'])
                            except:
                                value = row['value']
                        if count % skip == 0:
                            rc[node['name']].append([row['timestamp'], value])
                        count += 1
                return rc

    def __init__(self):
        self.data = self.Data(self)
        self.data.exposed = True

        self.buderus = buderus.Buderus(host=cherrypy.config.get('buderus.host'), userPassword=cherrypy.config.get('buderus.userPassword'), gatewayPassword=cherrypy.config.get('buderus.gatewayPassword'))

    @staticmethod
    def databaseParameters():
        return {'parameters': {'user': cherrypy.config['database.username'], 'passwd': cherrypy.config['database.password'], 'db': cherrypy.config['database.name'], 'host': cherrypy.config['database.host'], 'charset': 'utf8'}}

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
            cursor.execute('SELECT * FROM buderusKeys')
            keyNames = cursor.fetchall()
            for node in nodes:
                data = json.loads(self.buderus.get_data(node))
                data['id'] = data['id'].lstrip('/').replace('/', '.')

                keyName = filter(lambda k: k['name'] == data['id'], keyNames)
                if len(keyName) == 0:
                    cursor.execute('INSERT INTO buderusKeys (name, unit) VALUES(%s, %s)', (data['id'], data.get('unitOfMeasure')))
                    keyId = cursor.lastrowid()
                else:
                    keyId = keyName[0]['id']

                cursor.execute('INSERT INTO buderusData (nameId, value) VALUES(%s, %s)', (keyId, data['value']))
            cursor.execute('COMMIT')

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

