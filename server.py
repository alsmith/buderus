#!/usr/bin/python

import os, sys
import argparse
import buderus
import cherrypy
import datetime
import io
import json
from PIL import Image
import re
import requests
import socket
import time

import helpers

class Root():
    favicon_ico = None

class API():
    class Data():
        def __init__(self, api):
            self.api = api
            self.PUT = helpers.notImplemented
            self.DELETE = helpers.notImplemented

        @cherrypy.tools.json_out(handler=helpers.dumper)
        def GET(self):
            #(user, readonly) = helpers.authorisedUser()
            #if not user:
            #    raise cherrypy.HTTPError(403)

            rc = {'status': []}
            with helpers.DatabaseCursor() as cursor:
                cursor.execute('SELECT * FROM buderusTimestamps, buderusKeys, buderusData WHERE timestampId = buderusTimestamps.id AND keyId = buderusKeys.id AND timestampId = (SELECT id FROM buderusTimestamps WHERE timestamp = (SELECT MAX(timestamp) FROM buderusTimestamps))')
                for row in cursor:
                    rc['timestamp'] = row['timestamp']
                    rc['status'].append({'name': row['name'],
                                         'value': row['numericValue'] if row['numericValue'] is not None else row['stringValue'],
                                         'unit': row['unit']})
                cursor.execute('SELECT * FROM buderusKeys, buderusRollup WHERE keyId = buderusKeys.id AND date = (SELECT DATE(MAX(timestamp)) FROM buderusTimestamps)')
                for row in cursor:
                    for status in rc['status']:
                        if status['name'] == row['name']:
                            status['minValue'] = row['minValue']
                            status['maxValue'] = row['maxValue']
            return rc

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
                date = datetime.datetime(year=int(m.group('year')), month=int(m.group('month')), day=int(m.group('day')))
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
                    dbKeys = {}
                    cursor.execute('SELECT * FROM buderusKeys')
                    for row in cursor:
                        dbKeys[row['id']] = row['name']

                    cursor.execute('SELECT timestamp, keyId, numericValue FROM buderusTimestamps, buderusData WHERE timestampId IN (SELECT id FROM buderusTimestamps WHERE DATE(timestamp) = DATE(%s)) AND buderusTimestamps.id = buderusData.timestampId ORDER BY timestamp ASC', (date,))
                    for row in cursor:
                        keyName = dbKeys[row['keyId']]
                        if keyName not in criteria['dayKeys']:
                            continue
                        if keyName not in rc:
                            rc[keyName] = []
                        rc[keyName].append([row['timestamp'], API.formatValue(row['numericValue'])])

                if 'yearKeys' in criteria:
                    cursor.execute('SELECT * FROM buderusKeys,buderusRollup WHERE YEAR(date) = YEAR(%s) AND buderusKeys.id = buderusRollup.keyId AND buderusKeys.name IN %s', (date, criteria['yearKeys']))
                    for row in cursor:
                        minKey = '%s.min' % row['name']
                        maxKey = '%s.max' % row['name']
                        sumKey = '%s.sum' % row['name']
                        deltaKey = '%s.delta' % row['name']
                        for k in [minKey, maxKey, sumKey, deltaKey]:
                            if k not in rc:
                                rc[k] = []
                        rc[minKey].append([row['date'], row['minValue']])
                        rc[maxKey].append([row['date'], row['maxValue']])
                        rc[sumKey].append([row['date'], row['sumValue']])
                        rc[deltaKey].append([row['date'], row['maxValue']-row['minValue']])

            if skip != 1:
                for series in rc.keys():
                    if series.endswith(('.min', '.max', '.sum', '.delta')):
                        continue
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
                return float(value)
            except:
                pass
            try:
                return int(value)
            except:
                pass
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

    @staticmethod
    def getRainfallPixel(img, x, y):
        pixel = img.getpixel((x, y))
        if pixel == (0,0,0,0):
            mmh = 0
        elif pixel == (155, 125, 150, 255):
            mmh = 0.2
        elif pixel == (0,0,255,255):
            mmh = 1
        elif pixel == (5, 140, 45, 255): 
            mmh = 2
        elif pixel == (5, 255, 5, 255):
            mmh = 4
        elif pixel == (255, 255, 0, 255):
            mmh = 6
        elif pixel == (255, 200, 0, 255):
            mmh = 10
        elif pixel == (255, 125, 0, 255):
            mmh = 20
        elif pixel == (255, 25, 0, 255):
            mmh = 40
        elif pixel == (175, 0, 220, 255):
            mmh = 60
        else:
            mmh = pixel[0]*256*256+pixel[1]*256+pixel[2]
        return mmh

    @staticmethod
    def getRainfall(node):
        r = requests.get('http://www.meteoschweiz.admin.ch/home.html?tab=rain')
        m = re.match('.*data-json-url="(?P<url>/product/output/radar/version__\d{8}_\d{4}/de/animation.json)".*', r.content, re.DOTALL)

        r = requests.get('http://www.meteoschweiz.admin.ch%s' % m.group('url'))
        animation = json.loads(r.text)
        # These pixel values are for Switzerland, 4125-Riehen.
        #
        # If you also live in Switzerland then you'll have to work
        # out our own pixel value based on the animation images
        # received... if you don't live in Switzerland then
        # you'll be better off turning the rainfall feature off
        # altogether. Sorry about that!
        x = 361
        y = 209

        for image in animation['radar_images'][0]['pictures']:
            if image['data_type'] == 'measurement':
                r = requests.get('http://www.meteoschweiz.admin.ch/%s' % image['url'], headers = {'Referer': 'http://www.meteoschweiz.admin.ch/home.html?tab=rain'})
                with io.BytesIO(r.content) as png:
                    with Image.open(png) as img:
                        mmh = API.getRainfallPixel(img, x, y)
                        mmh += API.getRainfallPixel(img, x+1, y)*0.5
                        mmh += API.getRainfallPixel(img, x, y+1)*0.5
                        mmh += API.getRainfallPixel(img, x-1, y)*0.5
                        mmh += API.getRainfallPixel(img, x, y-1)*0.5
                        mmh += API.getRainfallPixel(img, x+1, y+1)*0.25
                        mmh += API.getRainfallPixel(img, x+1, y-1)*0.25
                        mmh += API.getRainfallPixel(img, x-1, y+1)*0.25
                        mmh += API.getRainfallPixel(img, x-1, y-1)*0.25
                        mmh /= 4
        return json.dumps({'id': node, 'unitOfMeasure': 'mm/h', 'value': mmh})

    @staticmethod
    def updateSolarYield(cursor, date):
        cursor.execute('SELECT COUNT(*) AS count FROM buderusData WHERE timestampId >= (SELECT MIN(id) FROM buderusTimestamps WHERE DATE(timestamp) = %s AND MINUTE(timestamp) > 10 AND MINUTE(timestamp) < 50) AND keyId = (SELECT id FROM buderusKeys WHERE name = %s)', (date, 'solarCircuits.sc1.solarYield'))
        if cursor.fetchone()['count'] > 0:
            cursor.execute('UPDATE buderusRollup SET sumValue = (SELECT SUM(value) AS `sumValue` FROM (SELECT AVG(numericValue) AS value, HOUR(timestamp), DATE(timestamp) AS date FROM buderusTimestamps, buderusData WHERE DATE(timestamp) = %s AND keyId = (SELECT id FROM buderusKeys WHERE name = %s) AND MINUTE(timestamp) > 10 AND MINUTE(timestamp) < 50 AND buderusTimestamps.id = buderusData.timestampId GROUP BY DATE(timestamp),HOUR(timestamp)) Y) WHERE `keyId` = (SELECT id FROM buderusKeys WHERE name = %s) AND `date` = %s', (date, 'solarCircuits.sc1.solarYield', 'solarCircuits.sc1.solarYield', date))

    def queryBoiler(self):
        nodes = [ '/gateway/versionFirmware',
                  '/system/sensors/temperatures/outdoor_t1',
                  '/system/sensors/temperatures/supply_t1',
                  '/system/sensors/temperatures/return',
                  '/system/sensors/temperatures/hotWater_t2',
                  '/system/sensors/temperatures/switch',
                  '/system/appliance/nominalBurnerLoad',

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
                  '/heatSources/supplyTemperatureSetpoint',

                  '/heatingCircuits/hc1/actualSupplyTemperature',
                  '/heatingCircuits/hc1/operationMode',
                  '/heatingCircuits/hc1/temperatureLevels/eco',
                  '/heatingCircuits/hc1/temperatureLevels/comfort2',
                  '/heatingCircuits/hc1/temporaryRoomSetpoint',
                  '/heatingCircuits/hc1/roomtemperature',
                  '/heatingCircuits/hc1/pumpModulation',
                  '/heatingCircuits/hc1/fastHeatupFactor',

                  '/dhwCircuits/dhw1/actualTemp',
                  '/dhwCircuits/dhw1/workingTime',
                  '/dhwCircuits/dhw1/temperatureLevels/high',

                  '/solarCircuits/sc1/dhwTankTemperature',
                  '/solarCircuits/sc1/solarYield',
                  '/solarCircuits/sc1/pumpModulation',
                  '/solarCircuits/sc1/collectorTemperature',
                  '/solarCircuits/sc1/actuatorStatus',

                  # XXX/ajs need to make this configurable in config.ini
                  '/rainfall', # Remove if not required.
                ]

        with helpers.DatabaseCursor(maxErrors=None) as cursor:
            cursor.execute('INSERT INTO buderusTimestamps () VALUES()')
            timestampId = cursor.lastrowid()

            cursor.execute('SELECT * FROM buderusKeys')
            keyNames = cursor.fetchall()
            for node in nodes:
                if node == '/rainfall':
                    jsonText = API.getRainfall(node)
                else:
                    jsonText = self.buderus.get_data(node)
                if not jsonText:
                    continue
                data = json.loads(jsonText)
                data['id'] = data['id'].lstrip('/').replace('/', '.')

                value = API.formatValue(data['value'])
                if type(value) in (int, float):
                    field = 'numericValue'
                else:
                    field = 'stringValue'

                keyName = filter(lambda k: k['name'] == data['id'], keyNames)
                if len(keyName) == 0:
                    cursor.execute('INSERT INTO buderusKeys (name, `numeric`, unit) VALUES(%s, %s, %s)', (data['id'], field == 'numericValue', data.get('unitOfMeasure')))
                    keyId = cursor.lastrowid()
                else:
                    keyId = keyName[0]['id']

                # Sometimes this key reports as zero... ignore such values
                if data['id'] == 'heatSources.numberOfStarts' and value == 0:
                    continue

                cursor.execute('INSERT INTO buderusData (timestampId, keyId, %s) VALUES(%%s, %%s, %%s)' % field, (timestampId, keyId, API.formatValue(data['value'])))

            # Compute potentially missing burner data...
            cursor.execute('SELECT buderusData.id,name,numericValue FROM buderusData,buderusKeys WHERE timestampId = %s AND buderusData.keyId = buderusKeys.id AND `numeric` = 1', (timestampId,))
            datapoints = {}
            for row in cursor:
                datapoints[row['name']] = {'id': row['id'], 'value': row['numericValue']}
            if datapoints['heatSources.actualPower']['value'] == 0 and datapoints['heatSources.actualModulation']['value'] != 0:
                datapoints['heatSources.actualPower']['value'] = datapoints['system.appliance.nominalBurnerLoad']['value'] * datapoints['heatSources.actualModulation']['value'] / 100
                cursor.execute('UPDATE buderusData SET numericValue = %s WHERE id = %s', (datapoints['heatSources.actualPower']['value'], datapoints['heatSources.actualPower']['id']))

            # Find missing dates...
            cursor.execute('SELECT DISTINCT DATE(timestamp) AS date FROM buderusTimestamps WHERE DATE(timestamp) NOT IN (SELECT DISTINCT date FROM buderusRollup) ORDER BY DATE(timestamp) ASC')
            dates = set(map(lambda row: row['date'], cursor.fetchall()))

            # ...and populate the rollup table with any missing days' data.
            for date in dates:
                cursor.execute('SELECT id FROM buderusTimestamps WHERE DATE(timestamp) = %s', (date,))
                timestampIDs = map(lambda row: row['id'], cursor.fetchall())
                if timestampIDs:
                    cursor.execute('DELETE FROM buderusRollup WHERE `date` = %s', (date,))
                    cursor.execute('INSERT INTO buderusRollup (`date`, `keyId`, `minValue`, `maxValue`, `sumValue`) SELECT %s AS date, keyId, MIN(numericValue) AS `minValue`, MAX(numericValue) AS `maxValue`, 0 as `sumValue` FROM buderusKeys, buderusData WHERE timestampId IN %s AND buderusKeys.id = keyId AND buderusKeys.numeric GROUP BY keyId', (date, timestampIDs))

                self.updateSolarYield(cursor, date)

            # Now update today's data in a less expensive fashion.
            cursor.execute('SELECT `buderusRollup`.`id`, `name`, `minValue`, `maxValue` FROM buderusKeys,buderusRollup WHERE keyId = buderusKeys.id AND `date` = (SELECT DATE(timestamp) FROM buderusTimestamps WHERE id = %s)', (timestampId,))
            if cursor.rowcount() == 0:
                # Ought to never happen, as a missing day will have been identified in
                # the loop above and initial data inserted.
                cursor.execute('INSERT INTO buderusRollup (`date`, `keyId`, `minValue`, `maxValue`, `sumValue`) SELECT (SELECT date(timestamp) FROM buderusTimestamps WHERE id = %s) AS date, keyId, numericValue AS `minValue`, numericValue AS `maxValue`, 0 AS `sumValue` FROM buderusKeys, buderusData WHERE timestampId = %s AND buderusKeys.id = keyId AND buderusKeys.numeric', (timestampId, timestampId))
            else:
                rollups = cursor.fetchall()
                for rollup in rollups:
                    currentValue = datapoints[rollup['name']]['value']
                    cursor.execute('UPDATE buderusRollup SET `minValue` = LEAST(%s,%s),`maxValue` = GREATEST(%s,%s) WHERE id = %s', (currentValue, rollup['minValue'], currentValue, rollup['maxValue'], rollup['id'],))

            self.updateSolarYield(cursor, datetime.date.today())

def main():
    parser = argparse.ArgumentParser(usage='usage: %s' % os.path.basename(__file__))

    parser.add_argument('--foreground', action='store_true', help='Don\'t daemonize')
    parser.add_argument('--config', default=os.path.join(os.path.dirname(__file__), 'config.ini'), help='Path to config.ini')
    args = parser.parse_args()

    if not args.config:
        print 'config.ini file not specified'
        return 1

    if not args.foreground:
        cherrypy.process.plugins.Daemonizer(cherrypy.engine).subscribe()

    os.chdir(os.path.dirname(__file__))
    cherrypy.config.update(args.config)
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

