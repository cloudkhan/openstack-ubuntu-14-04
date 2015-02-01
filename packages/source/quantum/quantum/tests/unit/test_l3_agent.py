# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Nicira, Inc.
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

import copy

import mock
from oslo.config import cfg

from quantum.agent.common import config as agent_config
from quantum.agent import l3_agent
from quantum.agent.linux import interface
from quantum.common import config as base_config
from quantum.common import constants as l3_constants
from quantum.openstack.common import uuidutils
from quantum.tests import base


_uuid = uuidutils.generate_uuid
HOSTNAME = 'myhost'


class TestBasicRouterOperations(base.BaseTestCase):

    def setUp(self):
        super(TestBasicRouterOperations, self).setUp()
        self.conf = cfg.ConfigOpts()
        self.conf.register_opts(base_config.core_opts)
        self.conf.register_opts(l3_agent.L3NATAgent.OPTS)
        agent_config.register_root_helper(self.conf)
        self.conf.register_opts(interface.OPTS)
        self.conf.set_override('interface_driver',
                               'quantum.agent.linux.interface.NullDriver')
        self.conf.root_helper = 'sudo'

        self.device_exists_p = mock.patch(
            'quantum.agent.linux.ip_lib.device_exists')
        self.device_exists = self.device_exists_p.start()

        self.utils_exec_p = mock.patch(
            'quantum.agent.linux.utils.execute')
        self.utils_exec = self.utils_exec_p.start()

        self.external_process_p = mock.patch(
            'quantum.agent.linux.external_process.ProcessManager')
        self.external_process = self.external_process_p.start()

        self.dvr_cls_p = mock.patch('quantum.agent.linux.interface.NullDriver')
        driver_cls = self.dvr_cls_p.start()
        self.mock_driver = mock.MagicMock()
        self.mock_driver.DEV_NAME_LEN = (
            interface.LinuxInterfaceDriver.DEV_NAME_LEN)
        driver_cls.return_value = self.mock_driver

        self.ip_cls_p = mock.patch('quantum.agent.linux.ip_lib.IPWrapper')
        ip_cls = self.ip_cls_p.start()
        self.mock_ip = mock.MagicMock()
        ip_cls.return_value = self.mock_ip

        self.l3pluginApi_cls_p = mock.patch(
            'quantum.agent.l3_agent.L3PluginApi')
        l3pluginApi_cls = self.l3pluginApi_cls_p.start()
        self.plugin_api = mock.Mock()
        l3pluginApi_cls.return_value = self.plugin_api

    def tearDown(self):
        self.device_exists_p.stop()
        self.l3pluginApi_cls_p.stop()
        self.ip_cls_p.stop()
        self.dvr_cls_p.stop()
        self.utils_exec_p.stop()
        self.external_process_p.stop()
        super(TestBasicRouterOperations, self).tearDown()

    def testRouterInfoCreate(self):
        id = _uuid()
        ri = l3_agent.RouterInfo(id, self.conf.root_helper,
                                 self.conf.use_namespaces, None)

        self.assertTrue(ri.ns_name().endswith(id))

    def testAgentCreate(self):
        l3_agent.L3NATAgent(HOSTNAME, self.conf)

    def _test_internal_network_action(self, action):
        port_id = _uuid()
        router_id = _uuid()
        network_id = _uuid()
        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces, None)
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        cidr = '99.0.1.9/24'
        mac = 'ca:fe:de:ad:be:ef'
        ex_gw_port = {'fixed_ips': [{'ip_address': '20.0.0.30'}]}

        if action == 'add':
            self.device_exists.return_value = False
            agent.internal_network_added(ri, ex_gw_port, network_id,
                                         port_id, cidr, mac)
            self.assertEqual(self.mock_driver.plug.call_count, 1)
            self.assertEqual(self.mock_driver.init_l3.call_count, 1)
        elif action == 'remove':
            self.device_exists.return_value = True
            agent.internal_network_removed(ri, ex_gw_port, port_id, cidr)
            self.assertEqual(self.mock_driver.unplug.call_count, 1)
        else:
            raise Exception("Invalid action %s" % action)

    def testAgentAddInternalNetwork(self):
        self._test_internal_network_action('add')

    def testAgentRemoveInternalNetwork(self):
        self._test_internal_network_action('remove')

    def _test_external_gateway_action(self, action):
        router_id = _uuid()
        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces, None)
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        internal_cidrs = ['100.0.1.0/24', '200.74.0.0/16']
        ex_gw_port = {'fixed_ips': [{'ip_address': '20.0.0.30',
                                     'subnet_id': _uuid()}],
                      'subnet': {'gateway_ip': '20.0.0.1'},
                      'id': _uuid(),
                      'network_id': _uuid(),
                      'mac_address': 'ca:fe:de:ad:be:ef',
                      'ip_cidr': '20.0.0.30/24'}
        interface_name = agent.get_external_device_name(ex_gw_port['id'])

        if action == 'add':
            self.device_exists.return_value = False
            agent.external_gateway_added(ri, ex_gw_port, internal_cidrs)
            self.assertEqual(self.mock_driver.plug.call_count, 1)
            self.assertEqual(self.mock_driver.init_l3.call_count, 1)
            arping_cmd = ['arping', '-A', '-U',
                          '-I', interface_name,
                          '-c', self.conf.send_arp_for_ha,
                          '20.0.0.30']
            if self.conf.use_namespaces:
                self.mock_ip.netns.execute.assert_any_call(
                    arping_cmd, check_exit_code=True)
            else:
                self.utils_exec.assert_any_call(
                    check_exit_code=True, root_helper=self.conf.root_helper)

        elif action == 'remove':
            self.device_exists.return_value = True
            agent.external_gateway_removed(ri, ex_gw_port, internal_cidrs)
            self.assertEqual(self.mock_driver.unplug.call_count, 1)
        else:
            raise Exception("Invalid action %s" % action)

    def testAgentAddExternalGateway(self):
        self._test_external_gateway_action('add')

    def testAgentRemoveExternalGateway(self):
        self._test_external_gateway_action('remove')

    def _test_floating_ip_action(self, action):
        router_id = _uuid()
        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces, None)
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        floating_ip = '20.0.0.100'
        fixed_ip = '10.0.0.23'
        ex_gw_port = {'fixed_ips': [{'ip_address': '20.0.0.30',
                                     'subnet_id': _uuid()}],
                      'subnet': {'gateway_ip': '20.0.0.1'},
                      'id': _uuid(),
                      'mac_address': 'ca:fe:de:ad:be:ef',
                      'ip_cidr': '20.0.0.30/24'}
        interface_name = agent.get_external_device_name(ex_gw_port['id'])

        if action == 'add':
            self.device_exists.return_value = False
            agent.floating_ip_added(ri, ex_gw_port, floating_ip, fixed_ip)
            arping_cmd = ['arping', '-A', '-U',
                          '-I', interface_name,
                          '-c', self.conf.send_arp_for_ha,
                          floating_ip]
            if self.conf.use_namespaces:
                self.mock_ip.netns.execute.assert_any_call(
                    arping_cmd, check_exit_code=True)
            else:
                self.utils_exec.assert_any_call(
                    check_exit_code=True, root_helper=self.conf.root_helper)

        elif action == 'remove':
            self.device_exists.return_value = True
            agent.floating_ip_removed(ri, ex_gw_port, floating_ip, fixed_ip)
        else:
            raise Exception("Invalid action %s" % action)

    def testAgentAddFloatingIP(self):
        self._test_floating_ip_action('add')

    def testAgentRemoveFloatingIP(self):
        self._test_floating_ip_action('remove')

    def _check_agent_method_called(self, agent, calls, namespace):
        if namespace:
            self.mock_ip.netns.execute.assert_has_calls(
                [mock.call(call, check_exit_code=False) for call in calls],
                any_order=True)
        else:
            self.utils_exec.assert_has_calls([
                mock.call(call, root_helper='sudo',
                          check_exit_code=False) for call in calls],
                any_order=True)

    def _test_routing_table_update(self, namespace):
        if not namespace:
            self.conf.set_override('use_namespaces', False)

        router_id = _uuid()
        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces,
                                 None)
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)

        fake_route1 = {'destination': '135.207.0.0/16',
                       'nexthop': '1.2.3.4'}
        fake_route2 = {'destination': '135.207.111.111/32',
                       'nexthop': '1.2.3.4'}

        agent._update_routing_table(ri, 'replace', fake_route1)
        expected = [['ip', 'route', 'replace', 'to', '135.207.0.0/16',
                     'via', '1.2.3.4']]
        self._check_agent_method_called(agent, expected, namespace)

        agent._update_routing_table(ri, 'delete', fake_route1)
        expected = [['ip', 'route', 'delete', 'to', '135.207.0.0/16',
                     'via', '1.2.3.4']]
        self._check_agent_method_called(agent, expected, namespace)

        agent._update_routing_table(ri, 'replace', fake_route2)
        expected = [['ip', 'route', 'replace', 'to', '135.207.111.111/32',
                     'via', '1.2.3.4']]
        self._check_agent_method_called(agent, expected, namespace)

        agent._update_routing_table(ri, 'delete', fake_route2)
        expected = [['ip', 'route', 'delete', 'to', '135.207.111.111/32',
                     'via', '1.2.3.4']]
        self._check_agent_method_called(agent, expected, namespace)

    def testAgentRoutingTableUpdated(self):
        self._test_routing_table_update(namespace=True)

    def testAgentRoutingTableUpdatedNoNameSpace(self):
        self._test_routing_table_update(namespace=False)

    def testRoutesUpdated(self):
        self._test_routes_updated(namespace=True)

    def testRoutesUpdatedNoNamespace(self):
        self._test_routes_updated(namespace=False)

    def _test_routes_updated(self, namespace=True):
        if not namespace:
            self.conf.set_override('use_namespaces', False)
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        router_id = _uuid()

        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces,
                                 None)
        ri.router = {}

        fake_old_routes = []
        fake_new_routes = [{'destination': "110.100.31.0/24",
                            'nexthop': "10.100.10.30"},
                           {'destination': "110.100.30.0/24",
                            'nexthop': "10.100.10.30"}]
        ri.routes = fake_old_routes
        ri.router['routes'] = fake_new_routes
        agent.routes_updated(ri)

        expected = [['ip', 'route', 'replace', 'to', '110.100.30.0/24',
                    'via', '10.100.10.30'],
                    ['ip', 'route', 'replace', 'to', '110.100.31.0/24',
                    'via', '10.100.10.30']]

        self._check_agent_method_called(agent, expected, namespace)

        fake_new_routes = [{'destination': "110.100.30.0/24",
                            'nexthop': "10.100.10.30"}]
        ri.router['routes'] = fake_new_routes
        agent.routes_updated(ri)
        expected = [['ip', 'route', 'delete', 'to', '110.100.31.0/24',
                    'via', '10.100.10.30']]

        self._check_agent_method_called(agent, expected, namespace)
        fake_new_routes = []
        ri.router['routes'] = fake_new_routes
        agent.routes_updated(ri)

        expected = [['ip', 'route', 'delete', 'to', '110.100.30.0/24',
                    'via', '10.100.10.30']]
        self._check_agent_method_called(agent, expected, namespace)

    def testProcessRouter(self):

        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        router_id = _uuid()
        ex_gw_port = {'id': _uuid(),
                      'network_id': _uuid(),
                      'fixed_ips': [{'ip_address': '19.4.4.4',
                                     'subnet_id': _uuid()}],
                      'subnet': {'cidr': '19.4.4.0/24',
                                 'gateway_ip': '19.4.4.1'}}
        internal_port = {'id': _uuid(),
                         'network_id': _uuid(),
                         'admin_state_up': True,
                         'fixed_ips': [{'ip_address': '35.4.4.4',
                                        'subnet_id': _uuid()}],
                         'mac_address': 'ca:fe:de:ad:be:ef',
                         'subnet': {'cidr': '35.4.4.0/24',
                                    'gateway_ip': '35.4.4.1'}}

        fake_floatingips1 = {'floatingips': [
            {'id': _uuid(),
             'floating_ip_address': '8.8.8.8',
             'fixed_ip_address': '7.7.7.7',
             'port_id': _uuid()}]}

        router = {
            'id': router_id,
            l3_constants.FLOATINGIP_KEY: fake_floatingips1['floatingips'],
            l3_constants.INTERFACE_KEY: [internal_port],
            'routes': [],
            'gw_port': ex_gw_port}
        ri = l3_agent.RouterInfo(router_id, self.conf.root_helper,
                                 self.conf.use_namespaces, router=router)
        agent.process_router(ri)

        # remap floating IP to a new fixed ip
        fake_floatingips2 = copy.deepcopy(fake_floatingips1)
        fake_floatingips2['floatingips'][0]['fixed_ip_address'] = '7.7.7.8'

        router[l3_constants.FLOATINGIP_KEY] = fake_floatingips2['floatingips']
        agent.process_router(ri)

        # remove just the floating ips
        del router[l3_constants.FLOATINGIP_KEY]
        agent.process_router(ri)

        # now no ports so state is torn down
        del router[l3_constants.INTERFACE_KEY]
        del router['gw_port']
        agent.process_router(ri)

    def testRoutersWithAdminStateDown(self):
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        self.plugin_api.get_external_network_id.return_value = None

        routers = [
            {'id': _uuid(),
             'admin_state_up': False,
             'external_gateway_info': {}}]
        agent._process_routers(routers)
        self.assertNotIn(routers[0]['id'], agent.router_info)

    def testSingleLoopRouterRemoval(self):
        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)
        self.plugin_api.get_external_network_id.return_value = None
        routers = [
            {'id': _uuid(),
             'admin_state_up': True,
             'routes': [],
             'external_gateway_info': {}}]
        agent._process_routers(routers)

        agent.router_deleted(None, routers[0]['id'])
        # verify that remove is called
        self.assertEqual(self.mock_ip.get_devices.call_count, 1)

        self.device_exists.assert_has_calls(
            [mock.call(self.conf.external_network_bridge)])

    def testDestroyNamespace(self):

        class FakeDev(object):
            def __init__(self, name):
                self.name = name

        self.mock_ip.get_namespaces.return_value = ['qrouter-foo',
                                                    'qrouter-bar']
        self.mock_ip.get_devices.return_value = [FakeDev('qr-aaaa'),
                                                 FakeDev('qgw-aaaa')]

        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)

        agent._destroy_router_namespace = mock.MagicMock()
        agent._destroy_router_namespaces()

        self.assertEqual(agent._destroy_router_namespace.call_count, 2)

    def testDestroyNamespaceWithRouterId(self):

        class FakeDev(object):
            def __init__(self, name):
                self.name = name

        self.conf.router_id = _uuid()

        namespaces = ['qrouter-foo', 'qrouter-' + self.conf.router_id]

        self.mock_ip.get_namespaces.return_value = namespaces
        self.mock_ip.get_devices.return_value = [FakeDev('qr-aaaa'),
                                                 FakeDev('qgw-aaaa')]

        agent = l3_agent.L3NATAgent(HOSTNAME, self.conf)

        agent._destroy_router_namespace = mock.MagicMock()
        agent._destroy_router_namespaces(self.conf.router_id)

        self.assertEqual(agent._destroy_router_namespace.call_count, 1)


class TestL3AgentEventHandler(base.BaseTestCase):

    def setUp(self):
        super(TestL3AgentEventHandler, self).setUp()
        cfg.CONF.register_opts(l3_agent.L3NATAgent.OPTS)
        cfg.CONF.set_override(
            'interface_driver', 'quantum.agent.linux.interface.NullDriver'
        )
        cfg.CONF.set_override('use_namespaces', True)
        agent_config.register_root_helper(cfg.CONF)

        self.device_exists_p = mock.patch(
            'quantum.agent.linux.ip_lib.device_exists')
        self.device_exists = self.device_exists_p.start()

        self.utils_exec_p = mock.patch(
            'quantum.agent.linux.utils.execute')
        self.utils_exec = self.utils_exec_p.start()

        self.drv_cls_p = mock.patch('quantum.agent.linux.interface.NullDriver')
        driver_cls = self.drv_cls_p.start()
        self.mock_driver = mock.MagicMock()
        self.mock_driver.DEV_NAME_LEN = (
            interface.LinuxInterfaceDriver.DEV_NAME_LEN)
        driver_cls.return_value = self.mock_driver

        self.l3_plugin_p = mock.patch(
            'quantum.agent.l3_agent.L3PluginApi')
        l3_plugin_cls = self.l3_plugin_p.start()
        self.plugin_api = mock.Mock()
        l3_plugin_cls.return_value = self.plugin_api

        self.external_process_p = mock.patch(
            'quantum.agent.linux.external_process.ProcessManager'
        )
        self.external_process = self.external_process_p.start()

        self.agent = l3_agent.L3NATAgent(HOSTNAME)

    def tearDown(self):
        self.device_exists_p.stop()
        self.utils_exec_p.stop()
        self.drv_cls_p.stop()
        self.l3_plugin_p.stop()
        self.external_process_p.stop()
        super(TestL3AgentEventHandler, self).tearDown()

    def test_spawn_metadata_proxy(self):
        router_id = _uuid()
        metadata_port = 8080
        ip_class_path = 'quantum.agent.linux.ip_lib.IPWrapper'

        cfg.CONF.set_override('metadata_port', metadata_port)
        cfg.CONF.set_override('log_file', 'test.log')
        cfg.CONF.set_override('debug', True)

        router_info = l3_agent.RouterInfo(
            router_id, cfg.CONF.root_helper, cfg.CONF.use_namespaces, None
        )

        self.external_process_p.stop()
        try:
            with mock.patch(ip_class_path) as ip_mock:
                self.agent._spawn_metadata_proxy(router_info)
                ip_mock.assert_has_calls([
                    mock.call(
                        'sudo',
                        'qrouter-' + router_id
                    ),
                    mock.call().netns.execute([
                        'quantum-ns-metadata-proxy',
                        mock.ANY,
                        '--router_id=%s' % router_id,
                        mock.ANY,
                        '--metadata_port=%s' % metadata_port,
                        '--debug',
                        '--log-file=quantum-ns-metadata-proxy%s.log' %
                        router_id
                    ])
                ])
        finally:
            self.external_process_p.start()
