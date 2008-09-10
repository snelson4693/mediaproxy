# Copyright (C) 2008 AG Projects
# Author: Ruud Klaver <ruud@ag-projects.com>
#

"""Implementation of database accounting"""

from collections import deque
from datetime import datetime
import cjson

from application import log
from application.process import process
from application.python.queue import EventQueue
from application.configuration import *

from sqlobject import SQLObject, connectionForURI, sqlhub
from sqlobject import StringCol, BLOBCol, DateTimeCol, DatabaseIndex
from sqlobject.dberrors import *

from mediaproxy import configuration_filename

class Config(ConfigSection):
    dburi = "mysql://mediaproxy:CHANGEME@localhost/mediaproxy"
    sessions_table = "media_sessions"
    callid_column = "call_id"
    fromtag_column = "from_tag"
    totag_column = "to_tag"
    start_time_column = "start_time"
    info_column = "info"
    pool_size = 1

configuration = ConfigFile(configuration_filename)
configuration.read_settings("Database", Config)


connection = connectionForURI(Config.dburi)
sqlhub.processConnection = connection


class MediaSessions(SQLObject):
    class sqlmeta:
        table = Config.sessions_table
        createSQL = {'mysql': 'ALTER TABLE %s ENGINE MyISAM' % Config.sessions_table}
        cacheValues = False
    call_id = StringCol(length=255, dbName=Config.callid_column, notNone=True)
    from_tag = StringCol(length=64, dbName=Config.fromtag_column, notNone=True)
    to_tag = StringCol(length=64, dbName=Config.totag_column, notNone=True)
    start_time = DateTimeCol(dbName=Config.start_time_column, notNone=True)
    info = BLOBCol(length=65535, dbName=Config.info_column)
    ## Indexes
    callid_idx = DatabaseIndex('call_id', 'from_tag', 'to_tag', unique=True)
    start_time_idx = DatabaseIndex('start_time')


try:
    MediaSessions.createTable(ifNotExists=True)
except OperationalError, e:
    log.error("cannot create the `%s' table: %s" % (Config.sessions_table, e))
    log.msg("please make sure that the `%s' user has the CREATE and ALTER rights on the `%s' database" % (connection.user, connection.db))
    log.msg("then restart the dispatcher, or you can create the table yourself using the following definition:")
    log.msg("----------------- >8 -----------------")
    sql, constraints = MediaSessions.createTableSQL()
    statements = ';\n'.join([sql] + constraints) + ';'
    log.msg(statements)
    log.msg("----------------- >8 -----------------")
    #raise RuntimeError(str(e))


class Accounting(object):

    def __init__(self):
        self.databases = deque(DatabaseAccounting() for i in range(Config.pool_size))

    def start(self):
        for db in self.databases:
            db.start()

    def do_accounting(self, stats):
        db = self.databases.popleft()
        db.put(stats)
        self.databases.append(db)

    def stop(self):
        for db in self.databases:
            db.stop()
        for db in self.databases:
            db.join()


class DatabaseAccounting(EventQueue):

    def __init__(self):
        EventQueue.__init__(self, self.do_accounting)

    def do_accounting(self, stats):
        sqlrepr = connection.sqlrepr
        names  = ', '.join([Config.callid_column, Config.fromtag_column, Config.totag_column, Config.start_time_column, Config.info_column])
        values = ', '.join((sqlrepr(v) for v in [stats["call_id"], stats["from_tag"], stats["to_tag"], datetime.fromtimestamp(stats["start_time"]), cjson.encode(stats)]))
        q = """INSERT INTO %s (%s) VALUES (%s)""" % (Config.sessions_table, names, values)
        try:
            connection.query(q)
        except DatabaseError, e:
            log.error("failed to insert record into database: %s" % e)

