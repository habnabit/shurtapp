from twisted.application.internet import TCPServer
from twisted.application import service
from twisted.internet import reactor
from twisted.web.server import Site

import tiedye
import shurts

site = Site(tiedye.CertDetailWSGIResource(reactor, reactor.getThreadPool(), shurts.app))

application = service.Application('shurts')

TCPServer(8880, site).setServiceParent(application)
TCPServer(8025, tiedye.PhotoSMTPFactory()).setServiceParent(application)
