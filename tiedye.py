"Tie-dye is twisted shurts."
from zope.interface import implements

from twisted.internet import defer, threads, utils
from twisted.python import log
from twisted.mail import smtp

from email.parser import FeedParser
import mimetypes
import tempfile
import shurts
import os

class InvalidMessageError(Exception):
    pass

def interaction(func):
    def wrap(*a, **kw):
        def interactionFunction():
            session = shurts.db.session()
            try:
                res = func(session, *a, **kw)
            except:
                session.rollback()
                raise
            else:
                session.commit()
                return res
            finally:
                shurts.db.session.remove()
        return threads.deferToThread(interactionFunction)
    return wrap

@interaction
def getPhoto(session, photo_id):
    return shurts.Photo.query.get(photo_id)

@interaction
def updatePhoto(session, photo, filename):
    session.add(photo)
    photo.filename = filename

@defer.inlineCallbacks
def processImage(infile):
    log.msg('processing %r' % (infile,))
    basename = os.path.basename(infile)
    photo = yield getPhoto(basename.split('-', 1)[0])
    if photo is None:
        os.remove(infile)
        log.msg('invalid filename on %r' % (infile,))
        return

    outfile = os.path.join(shurts.app.config['PHOTO_QUEUE_DEST'], basename)
    processingOutput = yield utils.getProcessOutput(
        '/bin/sh', ['photo-pipeline/process-image.sh', infile, outfile])
    if processingOutput:
        log.msg('output processing %r: %r' % (infile, processingOutput))
    yield updatePhoto(photo, basename)
    os.remove(infile)
    log.msg('processed %r to %r' % (infile, outfile))

class ProcessorState(object):
    def __init__(self):
        self.processing = set()

    def scan(self):
        inQueue = set(os.listdir(shurts.app.config['PHOTO_QUEUE_DIR']))
        for image in inQueue - self.processing:
            self.processing.add(image)
            d = processImage(os.path.join(shurts.app.config['PHOTO_QUEUE_DIR'], image))
            d.addErrback(log.err, 'error processing %r' % (image,))
            d.addCallback(lambda r, im: self.processing.discard(im), image)

def extract_first_image(email, photo_id):
    for part in email.walk():
        if part.get_content_maintype() != 'image':
            continue
        _, ext = os.path.splitext(part.get_filename())
        if not ext:
            ext = mimetypes.guess_extension(part.get_content_type())
        if not ext:
            raise InvalidMessageError("no discernable image type")
        fobj = tempfile.NamedTemporaryFile(prefix='%s-' % (photo_id,), suffix=ext, delete=False)
        fobj.write(part.get_payload(decode=True))
        fobj.close()
        return fobj.name
    raise InvalidMessageError('no attachment')

@interaction
def photoIDFromKey(session, key):
    pending = shurts.PendingPhoto.query.filter_by(key=key).one()
    photo = pending.as_photo()
    session.delete(pending)
    session.add(photo)
    session.flush()
    return photo.id

class PhotoMessageDelivery(object):
    implements(smtp.IMessageDelivery)

    def receivedHeader(self, helo, origin, recipients):
        return 'Received: unimportantly'

    def validateFrom(self, helo, origin):
        return origin

    def validateTo(self, user):
        if str(user.dest) == shurts.app.config['NOTES_EMAIL']:
            return PhotoMessage
        raise smtp.SMTPBadRcpt(user)

class PhotoMessage(object):
    implements(smtp.IMessage)

    def __init__(self):
        self.email = FeedParser()

    def lineReceived(self, line):
        self.email.feed(line + '\n')

    @defer.inlineCallbacks
    def eomReceived(self):
        email = self.email.close()
        photo_id = yield photoIDFromKey(email['Subject'])
        fname = extract_first_image(email, photo_id)
        yield processImage(fname)

class PhotoSMTPFactory(smtp.SMTPFactory):
    protocol = smtp.SMTP

    def __init__(self):
        smtp.SMTPFactory.__init__(self)

    def buildProtocol(self, addr):
        p = smtp.SMTPFactory.buildProtocol(self, addr)
        p.delivery = PhotoMessageDelivery()
        return p

def main():
    from twisted.application import internet
    from twisted.application import service

    a = service.Application("shurts twisted daemon")
    processor = ProcessorState()
    internet.TimerService(20, processor.scan).setServiceParent(a)
    internet.TCPServer(8025, PhotoSMTPFactory()).setServiceParent(a)

    return a

application = main()
