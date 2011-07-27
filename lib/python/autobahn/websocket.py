###############################################################################
##
##  Copyright 2011 Tavendo GmbH
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

from twisted.internet import reactor, protocol
import binascii
import hashlib
import base64
import struct
import random


class HttpException():
   def __init__(self, code, reason):
      self.code = code
      self.reason = reason


class WebSocketServiceConnection(protocol.Protocol):

   STATE_CLOSED = 0
   STATE_CONNECTING = 1
   STATE_OPEN = 2

   CLOSE_STATUS_CODE_NORMAL = 1000
   CLOSE_STATUS_CODE_GOING_AWAY = 1001
   CLOSE_STATUS_CODE_PROTOCOL_ERROR = 1002
   CLOSE_STATUS_CODE_PAYLOAD_NOT_ACCEPTED = 1003
   CLOSE_STATUS_CODE_FRAME_TOO_LARGE = 1004
   CLOSE_STATUS_CODE_NULL = 1005 # MUST NOT be set in close frame!
   CLOSE_STATUS_CODE_CONNECTION_LOST = 1006 # MUST NOT be set in close frame!
   CLOSE_STATUS_CODE_TEXT_FRAME_NOT_UTF8 = 1007

   ##
   ## The following set of methods is intended to be overridden in subclasses
   ##

   def onConnect(self, host, uri, origin, protocols):
      """
      Callback when new WebSocket client connection is established.
      Throw HttpException when you don't want to accept WebSocket connection.
      Override in derived class.
      """
      pass

   def onOpen(self):
      """
      Callback when initial handshake was completed. Now you may send messages!
      Override in derived class.
      """
      pass

   def onMessage(self, msg, binary):
      """
      Callback when message was received.
      Override in derived class.
      """
      pass

   def onPing(self, payload):
      """
      Override in derived class.
      """
      pass

   def onPong(self, payload):
      """
      Override in derived class.
      """
      pass

   def onClose(self):
      """
      Override in derived class.
      """
      pass


   def __init__(self):
      self.state = WebSocketServiceConnection.STATE_CLOSED


   def connectionMade(self):
      self.debug = self.factory.debug
      self.peer = self.transport.getPeer()
      if self.debug:
         print self.peer, "connection accepted"
      self.state = WebSocketServiceConnection.STATE_CONNECTING
      self.data = ""
      self.msg_payload = []
      self.http_request = None
      self.http_headers = {}


   def connectionLost(self, reason):
      if self.debug:
         print self.peer, "connection lost"
      self.state = WebSocketServiceConnection.STATE_CLOSED


   def dataReceived(self, data):
      if self.debug:
         print self.peer, "data received", binascii.b2a_hex(data)

      self.data += data

      ## WebSocket is open (handshake was completed)
      ##
      if self.state == WebSocketServiceConnection.STATE_OPEN:

         while self.processData():
            pass

      ## WebSocket needs handshake
      ##
      elif self.state == WebSocketServiceConnection.STATE_CONNECTING:

         self.processHandshake()

      ## should not arrive here (invalid state)
      ##
      else:
         #print binascii.b2a_hex(data)
         raise Exception("invalid state")


   def processHandshake(self):

      ## only proceed when we have fully received the HTTP request line and all headers
      ##
      end_of_header = self.data.find("\x0d\x0a\x0d\x0a")
      if end_of_header >= 0:

         ## extract HTTP headers
         ##
         ## FIXME: properly handle headers split accross multiple lines
         ##
         raw = self.data[:end_of_header].splitlines()
         self.http_request = raw[0].strip()
         for h in raw[1:]:
            i = h.find(":")
            if i > 0:
               key = h[:i].strip()
               value = h[i+1:].strip()
               self.http_headers[key] = value

         ## remember rest (after HTTP headers, if any)
         ##
         self.data = self.data[end_of_header + 4:]

         ## self.http_request & self.http_headers are now set
         ## => validate WebSocket handshake
         ##

         #print self.http_request
         #print self.http_headers

         ## HTTP Request line : METHOD, VERSION
         ##
         rl = self.http_request.split(" ")
         if len(rl) != 3:
            return self.sendHttpBadRequest("bad HTTP request line '%s'" % self.http_request)
         if rl[0] != "GET":
            return self.sendHttpBadRequest("illegal HTTP method '%s'" % rl[0])
         vs = rl[2].split("/")
         if len(vs) != 2 or vs[0] != "HTTP" or vs[1] not in ["1.1"]:
            return self.sendHttpBadRequest("bad HTTP version '%s'" % rl[2])

         ## HTTP Request line : REQUEST-URI
         ##
         ## FIXME: checking
         ##
         self.http_request_uri = rl[1]

         ## Host
         ##
         ## FIXME: checking
         ##
         if not self.http_headers.has_key("Host"):
            return self.sendHttpBadRequest("HTTP Host header missing")
         self.http_request_host = self.http_headers["Host"].strip()

         ## Upgrade
         ##
         if not self.http_headers.has_key("Upgrade"):
            return self.sendHttpBadRequest("HTTP Upgrade header missing")
         if self.http_headers["Upgrade"] != "websocket":
            return self.sendHttpBadRequest("HTTP Upgrade header different from 'websocket'")

         ## Connection
         ##
         if not self.http_headers.has_key("Connection"):
            return self.sendHttpBadRequest("HTTP Connection header missing")
         connectionUpgrade = False
         for c in self.http_headers["Connection"].split(","):
            if c.strip() == "Upgrade":
               connectionUpgrade = True
               break
         if not connectionUpgrade:
            return self.sendHttpBadRequest("HTTP Connection header does not include 'Upgrade' value")

         ## Sec-WebSocket-Version
         ##
         if not self.http_headers.has_key("Sec-WebSocket-Version"):
            return self.sendHttpBadRequest("HTTP Sec-WebSocket-Version header missing")
         try:
            version = int(self.http_headers["Sec-WebSocket-Version"])
            if version < 8:
               return self.sendHttpBadRequest("Sec-WebSocket-Version %d not supported (only >= 8)" % version)
            else:
               self.websocket_version = version
         except:
            return self.sendHttpBadRequest("could not parse HTTP Sec-WebSocket-Version header ''" % self.http_headers["Sec-WebSocket-Version"])

         ## Sec-WebSocket-Protocol
         ##
         ## FIXME: checking
         ##
         if self.http_headers.has_key("Sec-WebSocket-Protocol"):
            protocols = self.http_headers["Sec-WebSocket-Protocol"].split(",")
            self.websocket_protocols = protocols
         else:
            self.websocket_protocols = []

         ## Sec-WebSocket-Origin
         ## http://tools.ietf.org/html/draft-ietf-websec-origin-02
         ##
         ## FIXME: checking
         ##
         if self.http_headers.has_key("Sec-WebSocket-Origin"):
            origin = self.http_headers["Sec-WebSocket-Origin"].strip()
            self.websocket_origin = origin
         else:
            self.websocket_origin = None

         ## Sec-WebSocket-Extensions
         ##
         if self.http_headers.has_key("Sec-WebSocket-Extensions"):
            pass

         ## Sec-WebSocket-Key
         ## http://tools.ietf.org/html/rfc4648#section-4
         ##
         if not self.http_headers.has_key("Sec-WebSocket-Key"):
            return self.sendHttpBadRequest("HTTP Sec-WebSocket-Version header missing")
         key = self.http_headers["Sec-WebSocket-Key"].strip()
         if len(key) != 24: # 16 bytes => (ceil(128/24)*24)/6 == 24
            return self.sendHttpBadRequest("bad Sec-WebSocket-Key (length must be 24 ASCII chars) '%s'" % key)
         if key[-2:] != "==": # 24 - ceil(128/6) == 2
            return self.sendHttpBadRequest("bad Sec-WebSocket-Key (invalid base64 encoding) '%s'" % key)
         for c in key[:-2]:
            if c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/":
               return self.sendHttpBadRequest("bad character '%s' in Sec-WebSocket-Key (invalid base64 encoding) '%s'" (c, key))

         ## WebSocket handshake validated
         ## => produce response

         # request Host, request URI, origin, cookies, protocols
         try:
            self.onConnect(self.http_request_host, self.http_request_uri, self.websocket_origin, self.websocket_protocols)
         except HttpException, e:
            return self.sendHttpRequestFailure(e.code, e.reason)

         ## compute Sec-WebSocket-Accept
         ##
         magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
         sha1 = hashlib.sha1()
         sha1.update(key + magic)
         sec_websocket_accept = base64.b64encode(sha1.digest())

         ## send response to complete WebSocket handshake
         ##
         response  = "HTTP/1.1 101 Switching Protocols\n"
         response += "Upgrade: websocket\n"
         response += "Connection: Upgrade\n"
         response += "Sec-WebSocket-Accept: %s\n" % sec_websocket_accept
         response += "Sec-WebSocket-Protocol: foobar\n"
         response += "\n"

         #print response
         self.transport.write(response)

         ## move into OPEN state
         ##
         self.state = WebSocketServiceConnection.STATE_OPEN
         self.current_msg = None
         self.current_frame = None

         self.onOpen()


   def sendHttpBadRequest(self, reason):
      self.sendHttpBadRequest(400, reason)


   def sendHttpRequestFailure(self, code, reason):
      #print "HTTP Request Failure", code, reason
      response  = "HTTP/1.1 %d %s\n" % (code, reason)
      response += "\n"

      #print response
      self.transport.write(response)
      self.transport.loseConnection()


   def processData(self):
      #print "WebSocketServiceConnection.processData"

      if self.current_frame is None:

         buffered_len = len(self.data)

         ## start of new frame
         ##
         if buffered_len >= 2: # need minimum frame length

            ## FIN, RSV, OPCODE
            ##
            b = ord(self.data[0])
            frame_fin = (b & 0x80) != 0
            frame_rsv = (b & 0x70) >> 4
            frame_opcode = b & 0x0f

            ## MASK, PAYLOAD LEN 1
            ##
            b = ord(self.data[1])
            frame_masked = (b & 0x80) != 0
            frame_payload_len1 = b & 0x7f

            ## MUST be 0 when no extension defining
            ## the semantics of RSV has been negotiated
            ##
            if frame_rsv != 0:
               self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "RSV != 0 and no extension negoiated")

            ## all client-to-server frames MUST be masked
            ##
            if not frame_masked:
               self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "unmasked client to server frame")

            ## check frame
            ##
            if frame_opcode > 7: # control frame (have MSB in opcode set)

               ## control frames MUST NOT be fragmented
               ##
               if not frame_fin:
                  self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "fragmented control frame")
               ## control frames MUST have payload 125 octets or less
               ##
               if frame_payload_len1 > 125:
                  self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "control frame with payload length > 125 octets")
               ## check for reserved control frame opcodes
               ##
               if frame_opcode not in [8, 9, 10]:
                  self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "control frame using reserved opcode %d" % frame_opcode)

            else: # data frame

               ## check for reserved data frame opcodes
               ##
               if frame_opcode not in [0, 1, 2]:
                  self.sendCloseFrame(WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR, "data frame using reserved opcode %d" % frame_opcode)

            ## compute complete header length
            ##
            if frame_payload_len1 <  126:
               frame_header_len = 2 + 4
            elif frame_payload_len1 == 126:
               frame_header_len = 2 + 2 + 4
            elif frame_payload_len1 == 127:
               frame_header_len = 2 + 8 + 4
            else:
               raise Exception("logic error")

            ## only proceed when we have enough data buffered for complete
            ## frame header (which includes extended payload len + mask)
            ##
            if buffered_len >= frame_header_len:

               i = 2

               ## EXTENDED PAYLOAD LEN
               ##
               if frame_payload_len1 == 126:
                  frame_payload_len = struct.unpack("!H", self.data[i:i+2])[0]
                  i += 2
               elif frame_payload_len1 == 127:
                  frame_payload_len = struct.unpack("!Q", self.data[i:i+8])[0]
                  i += 8
               else:
                  frame_payload_len = frame_payload_len1

               ## frame mask (all client-to-server frames MUST be masked!)
               ##
               frame_mask = []
               for j in range(i, i + 4):
                  frame_mask.append(ord(self.data[j]))
               i += 4

               self.current_frame = (frame_header_len, frame_fin, frame_rsv, frame_opcode, frame_masked, frame_mask, frame_payload_len)
               self.data = self.data[i:]
            else:
               return False # need more data
         else:
            return False # need more data

      if self.current_frame is not None:

         buffered_len = len(self.data)

         frame_header_len, frame_fin, frame_rsv, frame_opcode, frame_masked, frame_mask, frame_payload_len = self.current_frame

         if buffered_len >= frame_payload_len:

            ## unmask payload
            ##
            payload = ''.join([chr(ord(self.data[k]) ^ frame_mask[k % 4]) for k in range(0, frame_payload_len)])

            ## buffer rest and reset current_frame
            ##
            self.data = self.data[frame_payload_len:]
            self.current_frame = None

            ## now process frame
            ##
            self.onFrame(frame_fin, frame_opcode, payload)

            return len(self.data) > 0 # reprocess when buffered data left

         else:
            return False # need more data

      else:
         return False # need more data

               #print binascii.b2a_hex(data[2:2+4])
               #print binascii.b2a_hex(data[6:])


   def onFrame(self, fin, opcode, payload):

      ## DATA
      ##
      if opcode in [0, 1, 2]:

         self.msg_payload.append(payload)
         if fin:
            msg = ''.join(self.msg_payload)
            self.onMessage(msg, False)
            self.msg_payload = []

      ## CLOSE
      ##
      elif opcode == 8:

         code = None
         reason = None

         plen = len(payload)
         if plen > 0:

            ## If there is a body, the first two bytes of the body MUST be a 2-byte
            ## unsigned integer (in network byte order) representing a status code
            ##
            if plen < 2:
               pass
            code = struct.unpack("!H", payload[0:2])[0]

            ## Following the 2-byte integer the body MAY contain UTF-8
            ## encoded data with value /reason/, the interpretation of which is not
            ## defined by this specification.
            ##
            if plen > 2:
               try:
                  reason = unicode(payload[2:], 'utf8')
               except UnicodeDecodeError:
                  pass

         self._onclose(code, reason)

      ## PING
      ##
      elif opcode == 9:
         self.onPing(payload)

      ## PONG
      ##
      elif opcode == 10:
         self.onPong(payload)

      else:
         raise Exception("logic error processing frame with opcode %d" % opcode)


   def _onclose(self, status_code = None, status_reason = None):
      print "CLOSE received", status_code, status_reason


   def sendPing(self, payload):
      l = len(payload)
      if l > 125:
         raise Exception("invalid payload for PING (payload length must be <= 125, was %d)" % l)
      self.transport.write("\x89")
      self.transport.write(chr(l))
      if l > 0:
         self.transport.write(payload)


   def sendPong(self, payload):
      l = len(payload)
      if l > 125:
         raise Exception("invalid payload for PONG (payload length must be <= 125, was %d)" % l)
      self.transport.write("\x8a")
      self.transport.write(chr(l))
      if l > 0:
         self.transport.write(payload)


   def sendCloseFrame(self, status_code = None, status_reason = None):

      plen = 0
      code = None
      reason = None

      if status_code is not None:

         if (not (status_code >= 3000 and status_code <= 3999)) and \
            (not (status_code >= 4000 and status_code <= 4999)) and \
            status_code not in [WebSocketServiceConnection.CLOSE_STATUS_CODE_NORMAL,
                                WebSocketServiceConnection.CLOSE_STATUS_CODE_GOING_AWAY,
                                WebSocketServiceConnection.CLOSE_STATUS_CODE_PROTOCOL_ERROR,
                                WebSocketServiceConnection.CLOSE_STATUS_CODE_PAYLOAD_NOT_ACCEPTED,
                                WebSocketServiceConnection.CLOSE_STATUS_CODE_FRAME_TOO_LARGE,
                                WebSocketServiceConnection.CLOSE_STATUS_CODE_TEXT_FRAME_NOT_UTF8]:
            raise Exception("invalid status code %d for close frame" % status_code)

         code = struct.pack("!H", status_code)
         plen = len(code)

         if status_reason is not None:
            reason = status_reason.encode("UTF-8")
            plen += len(reason)

         if plen > 125:
            raise Exception("close frame payload larger than 125 octets")

      else:
         if status_reason is not None:
            raise Exception("status reason without status code in close frame")

      self.transport.write("\x88")
      self.transport.write(chr(plen))
      if code:
         self.transport.write(code)
         if reason:
            self.transport.write(reason)
      #self.transport.loseConnection()


   def sendMessage(self, payload, binary = False):

      if binary:
         self.sendFrame(opcode = 2, payload = payload)
      else:
         self.sendFrame(opcode = 1, payload = payload)


   def sendFrame(self, opcode, payload, fin = True, rsv = 0, mask = None, payload_len = None):

      ## This method deliberately allows to send invalid frames (that is frames invalid
      ## per-se, or frames invalid because of protocol state). Other than in fuzzing servers,
      ## calling methods will ensure that no invalid frames are sent.

      ## In addition, this method supports explicit specification of payload length.
      ## When payload_len is given, it will always write that many octets to the stream.
      ## It'll wrap within payload, resending parts of that when more octets were requested
      ## The use case is again for fuzzing server which want to sent increasing amounts
      ## of payload data to clients without having to construct potentially large messges
      ## themselfes.

      if payload_len:
         l = payload_len
      else:
         l = len(payload)

      ## first byte
      ##
      b0 = 0
      if fin:
         b0 |= (1 << 7)
      b0 |= (rsv % 8) << 4
      b0 |= opcode % 128

      ## second byte and payload len bytes
      ##
      b1 = 0
      el = ""
      if mask:
         b1 |= 1 << 7

      if l <= 125:
         b1 |= l
      elif l <= 0xFFFF:
         b1 |= 126
         el = struct.pack("!H", l)
      elif l <= 0x7FFFFFFFFFFFFFFF:
         b1 |= 127
         el = struct.pack("!Q", l)
      else:
         raise Exception("invalid payload length")

      self.transport.write(chr(b0))
      self.transport.write(chr(b1))

      self.transport.write(el)

      self.transport.write(payload)




class WebSocketService(protocol.ServerFactory):

   def __init__(self, debug = False):
      self.debug = debug
      self.protocol = WebSocketServiceConnection

   def startFactory(self):
      pass

   def stopFactory(self):
      pass
