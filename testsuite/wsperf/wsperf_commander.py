###############################################################################
##
##  Copyright 2012 Tavendo GmbH
##
##  Licensed under the Apache License, Version 2.0 (the "License");
##  you may not use this file except in compliance with the License.
##  You may obtain a copy of the License at
##
##      http://www.apache.org/licenses/LICENSE-2.0
##
##  Unless required by applicable law or agreed to in writing, software
##  distributed under the License is distributed on an "AS IS" BASIS,
##  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
##  See the License for the specific language governing permissions and
##  limitations under the License.
##
###############################################################################

import sys, json, pprint
from twisted.internet import reactor
from twisted.python import log, usage
from autobahn.websocket import WebSocketClientFactory, WebSocketClientProtocol, connectWS
from autobahn.util import newid

WSPERF_CMD = """message_test:uri=%(uri)s;token=%(token)s;size=%(size)d;count=%(count)d;timeout=10000;binary=true;sync=true;correctness=exact;"""

class WsPerfCommanderProtocol(WebSocketClientProtocol):

   def sendNext(self):
      if self.current == len(self.tests):
         return True
      test = self.tests[self.current]
      cmd = WSPERF_CMD % test
      if self.factory.debugWsPerf:
         print cmd
      #print "Starting test for testee %s" % test['name']
      sys.stdout.write('.')
      self.sendMessage(cmd)
      self.current += 1

   def setupTests(self):
      for server in factory.spec['servers']:
         for size in factory.spec['sizes']:
            id = newid()
            test = {'uri': server['uri'].encode('utf8'),
                    'name': server['name'].encode('utf8'),
                    'count': size[0],
                    'size': size[1],
                    'token': id}
            self.tests.append(test)
            self.testdefs[id] = test
      sys.stdout.write("Running %d tests against %d servers: " % (len(factory.spec['sizes']), len(factory.spec['servers'])))

   def onTestsComplete(self):
      print " - all tests finished."
      print
      if factory.debugWsPerf:
         self.pp.pprint(self.testresults)
      for test in self.tests:
         result = self.testresults[test['token']]
         median_microsecs = int(round(result['data']['median'] / 1000))
         print ','.join([str(x) for x in [test['name'], test['size'], median_microsecs]])
      reactor.stop()

   def onOpen(self):
      self.pp = pprint.PrettyPrinter(indent = 3)
      self.tests = []
      self.testdefs = {}
      self.testresults = {}
      self.current = 0
      self.setupTests()
      self.sendNext()

   def onMessage(self, msg, binary):
      if not binary:
         try:
            o = json.loads(msg)
            if o['type'] == u'test_complete':
               if self.sendNext():
                  self.onTestsComplete()
            elif o['type'] == u'test_data':
               if self.factory.debugWsPerf:
                  self.pp.pprint(o)
               self.testresults[o['token']] = o
         except ValueError, e:
            pass


class WsPerfCommanderOptions(usage.Options):
   optParameters = [
      ['wsperf', 'w', None, 'URI of wsperf running in master mode.']
   ]

   optFlags = [
      ['debug', 'd', 'Debug wsperf commander/protocol.']
   ]

   def postOptions(self):
      if not self['wsperf']:
         raise usage.UsageError, "need wsperf URI!"

if __name__ == '__main__':

   o = WsPerfCommanderOptions()
   try:
      o.parseOptions()
   except usage.UsageError, errortext:
      print '%s %s' % (sys.argv[0], errortext)
      print 'Try %s --help for usage details' % sys.argv[0]
      sys.exit(1)

   wsperf = str(o.opts['wsperf'])
   debug = o.opts['debug'] != 0

   #log.startLogging(sys.stdout)
   factory = WebSocketClientFactory(wsperf)
   factory.debugWsPerf = debug
   factory.spec = json.loads(open("wsperf_commander.json").read())
   factory.protocol = WsPerfCommanderProtocol
   connectWS(factory)
   reactor.run()
