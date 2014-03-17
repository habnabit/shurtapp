from twisted.application.internet import SSLServer, TCPServer
from twisted.application import service
from twisted.internet import ssl, reactor
from twisted.web.server import Site, NOT_DONE_YET

from OpenSSL import SSL
import tiedye
import shurts

site = Site(tiedye.CertDetailWSGIResource(reactor, reactor.getThreadPool(), shurts.app))
contextFac = ssl.DefaultOpenSSLContextFactory('server.key', 'server.crt')

def verifyCallback(connection, x509, errnum, errdepth, ok):
    return ok

ctx = contextFac.getContext()
ctx.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, verifyCallback)
ctx.load_verify_locations('ca.pem')

application = service.Application('shurts')

SSLServer(5443, site, contextFac).setServiceParent(application)
TCPServer(5000, site).setServiceParent(application)
TCPServer(8025, tiedye.PhotoSMTPFactory()).setServiceParent(application)
