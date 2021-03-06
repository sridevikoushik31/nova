# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2013 Rackspace Hosting
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
MySQLdb DB API implementation.

This will fall back to sqlalchemy for methods that are not yet implemented
here.
"""
import functools

import eventlet
from oslo.config import cfg

from nova import context
from nova.db.mysqldb import connection
from nova.db.mysqldb import exception as mysqldb_exc
from nova.db.mysqldb import models
from nova.db.sqlalchemy import api as sqlalchemy_api
from nova.openstack.common import log as logging


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def get_backend():
    """Return an instance of API to use as the backend."""
    return API()


def dbapi_method(retry=True, require_context=True,
                 require_admin_context=False):
    def wrapped(f):
        @functools.wraps(f)
        def inner(*args, **kwargs):
            if require_context:
                context.require_context(args[1])
            if require_admin_context:
                context.require_admin_context(args[1])
            while True:
                try:
                    result = f(*args, **kwargs)
                    break
                except mysqldb_exc.RetryableErrors as e:
                    if not retry:
                        raise
                    LOG.warning("Will retry DB API call '%(name)s' due to "
                                "exception: %(e)s",
                                dict(name=f.__name__, e=e))
                    eventlet.sleep(2)
            # Allow another greenthread to run
            eventlet.sleep(0)
            return result
        return inner
    return wrapped


class API(object):
    def __init__(self):
        self.pool = connection.ConnectionPool()
        self._launch_monitor()
        LOG.debug(_("MySQLDB API instantiated."))

    @dbapi_method(require_context=False)
    def _check_schema(self):
        with self.pool.get() as conn:
            schema = conn.get_schema()
            models.set_schema(schema)

    def _launch_monitor(self):
        def _schema_monitor():
            while True:
                eventlet.sleep(5)
                self._check_schema()
        self._check_schema()
        self._monitor_thread = eventlet.spawn_n(_schema_monitor)

    def __getattr__(self, key):
        # forward unimplemented method to sqlalchemy backend:
        LOG.warn(_("Falling back to SQLAlchemy for method %s") % key)
        return getattr(sqlalchemy_api, key)

    @staticmethod
    def constraint(**conditions):
        return models.Constraint(conditions)

    @staticmethod
    def equal_any(*values):
        return models.EqualityCondition(values)

    @staticmethod
    def not_equal(*values):
        return models.InequalityCondition(values)

    @dbapi_method()
    def bw_usage_update(self, ctxt, uuid, mac, start_period, bw_in, bw_out,
                        last_ctr_in, last_ctr_out, last_refreshed=None):
        with self.pool.get() as conn:
            return models.Models.BandwidthUsageCache.update(conn,
                    ctxt, uuid, mac, start_period, bw_in, bw_out,
                    last_ctr_in, last_ctr_out, last_refreshed)

    @dbapi_method()
    def instance_get_by_uuid(self, ctxt, instance_uuid):
        with self.pool.get() as conn:
            instance = models.Models.Instance.get_by_uuid(conn, ctxt,
                                                          instance_uuid)
        return instance.to_dict()

    @dbapi_method()
    def instance_get_all(self, ctxt, columns_to_join):
        with self.pool.get() as conn:
            instances = models.Models.Instance.get_all(conn, ctxt,
                                                       columns_to_join)
        return [i.to_dict() for i in instances]

    @dbapi_method()
    def instance_destroy(self, ctxt, instance_uuid, constraint=None):
        with self.pool.get() as conn:
            orig_instance = models.Models.Instance.destroy(conn, ctxt,
                                                           instance_uuid,
                                                           constraint)
        return orig_instance.to_dict()

    @dbapi_method()
    def instance_update(self, ctxt, instance_uuid, values):
        with self.pool.get() as conn:
            instance = models.Models.Instance.update(conn, ctxt,
                                                     instance_uuid, values)
        return instance.to_dict()

    @dbapi_method()
    def instance_update_and_get_original(self, ctxt, instance_uuid,
                                         values):
        method = models.Models.Instance.update_and_get_original
        with self.pool.get() as conn:
            orig_instance, instance = method(conn, ctxt, instance_uuid,
                                             values)
        return orig_instance.to_dict(), instance.to_dict()

    @dbapi_method()
    def instance_info_cache_get(self, ctxt, instance_uuid):
        method = models.Models.InstanceInfoCache.get
        with self.pool.get() as conn:
            info_cache = method(conn, ctxt, instance_uuid)
        if info_cache is not None:
            info_cache = info_cache.to_dict()
        return info_cache

    @dbapi_method()
    def instance_info_cache_update(self, ctxt, instance_uuid, values):
        method = models.Models.InstanceInfoCache.update
        with self.pool.get() as conn:
            info_cache = method(conn, ctxt, instance_uuid, values)
        if info_cache is not None:
            info_cache = info_cache.to_dict()
        return info_cache

    @dbapi_method()
    def instance_info_cache_delete(self, ctxt, instance_uuid):
        method = models.Models.InstanceInfoCache.soft_delete
        with self.pool.get() as conn:
            method(conn, instance_uuid)
