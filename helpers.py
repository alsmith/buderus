import cherrypy
import cPickle
import datetime
import json
import MySQLdb
import MySQLdb.cursors
import re
import socket
import subprocess
import time
import traceback

def dumper(*args, **kwargs):
    def helper(obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat().replace('T', ' ')
        return None

    value = cherrypy.serving.request._json_inner_handler(*args, **kwargs)
    return json.dumps(value, default=helper)

@cherrypy.tools.json_out()
def notImplemented():
    cherrypy.response.status = 501
    return {'exception': 'Not implemented'}

def log(msg, context, *args, **kwargs):
    cherrypy.log(msg, context)

class DatabaseCursor():
    def __init__(self, cursorClass=MySQLdb.cursors.DictCursor, maxErrors=5, autoCommit=True):
        self.errorCount = 0
        self.cursorClass = cursorClass
        self.maxErrors = maxErrors
        self.autoCommit = autoCommit

    def __enter__(self):
        self.cursor = self.testConnection()
        return self

    def __exit__(self, type, value, traceback):
        if self.autoCommit:
            self.cursor.execute('COMMIT')
        self.cursor.close()

    def __iter__(self):
        return self.cursor.__iter__()

    def next(self):
        return self.cursor.next()

    def connectToDatabase(self):
        return MySQLdb.connect(cursorclass=self.cursorClass, **cherrypy.thread_data.db['parameters'])

    def testConnection(self):
        if not cherrypy.thread_data.db['parameters']:
            return None

        while True:
            try:
                if 'connection' not in cherrypy.thread_data.db:
                    cherrypy.thread_data.db['connection'] = self.connectToDatabase()
                cursor = cherrypy.thread_data.db['connection'].cursor()
                cursor.execute('SELECT 0')
                cursor.fetchall()
                break
            except Exception as e:
                self.errorCount += 1
                cherrypy.log(msg='failure: %s' % str(e), context='MYSQL')
                if self.maxErrors is not None and self.errorCount == self.maxErrors:
                    raise
                if 'connection' in cherrypy.thread_data.db:
                    try:
                        cherrypy.thread_data.db['connection'].close()
                    except:
                        pass
                    del cherrypy.thread_data.db['connection']
                time.sleep(2)
        return cursor

    def execute(self, *args, **kwargs):
        return self.cursor.execute(*args, **kwargs)

    def fetchall(self, *args, **kwargs):
        return self.cursor.fetchall(*args, **kwargs)

    def fetchone(self, *args, **kwargs):
        return self.cursor.fetchone(*args, **kwargs)

    def lastrowid(self):
        return self.cursor.lastrowid

    def rowcount(self):
        return self.cursor.rowcount

class StubbornDBBackgroundTask(cherrypy.process.plugins.BackgroundTask):
    """
    The CherryPy default background task class quits at the first sign of an
    exception. We don't want that, so let's subclass it and overload it and
    don't just reraise the exception but carry on trying to run our function.
    """
    def __init__(self, db, interval, function, args=[], kwargs={}, bus=None):
        super(StubbornDBBackgroundTask, self).__init__(interval, function, args, kwargs, bus)
        self.db = db

    def run(self):
        cherrypy.thread_data.db = self.db()
        self.running = True
        while self.running:
            time.sleep(self.interval)
            if not self.running:
                return
            try:
                self.function(*self.args, **self.kwargs)
            except Exception as e:
                log(msg='Error in background task function: %s' % traceback.format_exc(), context='BACKGROUND')

