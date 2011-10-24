from zope.interface import implements

from twisted.enterprise import adbapi
from twisted.internet import defer
from twisted.mail import smtp

from email.parser import FeedParser
from functools import partial
import mimetypes
import datetime
import tempfile
import os

config = {}
execfile(os.environ['SHURT_SETTINGS'], config)

class InvalidMessageError(Exception):
    pass

def extract_first_image(email):
    for part in email.walk():
        if part.get_content_maintype() != 'image':
            continue
        basename, ext = os.path.splitext(part.get_filename())
        if not ext:
            ext = mimetypes.guess_extension(part.get_content_type())
        if not ext:
            raise InvalidMessageError("no discernable image type")
        fobj = tempfile.NamedTemporaryFile(prefix=basename,
                                           suffix=ext,
                                           dir=config['UPLOADED_PHOTOS_DEST'],
                                           delete=False)
        fobj.write(part.get_payload(decode=True))
        fobj.close()
        return os.path.basename(fobj.name)
    raise InvalidMessageError('no attachment')

def interaction(func):
    def wrap(self, *a, **kw):
        return self.dbpool.runInteraction(partial(func, self), *a, **kw)
    return wrap

class DatabaseRunner(object):
    def __init__(self):
        self.dbpool = config['ADBAPI_FACTORY'](adbapi.ConnectionPool)

    @interaction
    def checkKey(self, txn, key):
        txn.execute('SELECT id, type FROM pending_photos WHERE key = ? LIMIT 1', (key,))
        res = txn.fetchall()
        if not res:
            raise InvalidMessageError('invalid key: %r' % (key,))
        return res[0]

    @interaction
    def finishPhoto(self, txn, pending, filename):
        pending_id, pending_type = pending
        txn.execute('INSERT INTO photos ("when", filename, type) VALUES (?, ?, ?)',
                    (datetime.datetime.now().isoformat(' '),
                     filename,
                     pending_type))
        photo_id = txn.lastrowid
        if pending_type == 'shirt':
            txn.execute(
                'INSERT INTO shirt_photos SELECT ?, shirt_id FROM shirt_pending_photos WHERE pending_photo_id = ?',
                (photo_id, pending_id))
            txn.execute('DELETE FROM shirt_pending_photos WHERE pending_photo_id = ?', (pending_id,))
        elif pending_type == 'wearing':
            txn.execute(
                'INSERT INTO wearing_photos SELECT ?, wearing_id FROM wearing_pending_photos WHERE pending_photo_id = ?',
                (photo_id, pending_id))
            txn.execute('DELETE FROM wearing_pending_photos WHERE pending_photo_id = ?', (pending_id,))
        txn.execute('DELETE FROM pending_photos WHERE id = ?', (pending_id,))

class PhotoMessageDelivery(object):
    implements(smtp.IMessageDelivery)

    def receivedHeader(self, helo, origin, recipients):
        return 'Received: unimportantly'

    def validateFrom(self, helo, origin):
        return origin

    def validateTo(self, user):
        if str(user.dest) == config['NOTES_EMAIL']:
            return lambda: PhotoMessage(self.db)
        raise smtp.SMTPBadRcpt(user)

class PhotoMessage(object):
    implements(smtp.IMessage)

    def __init__(self, db):
        self.email = FeedParser()
        self.db = db

    def lineReceived(self, line):
        self.email.feed(line + '\n')

    @defer.inlineCallbacks
    def eomReceived(self):
        email = self.email.close()
        pending = yield self.db.checkKey(email['Subject'])
        fname = extract_first_image(email)
        yield self.db.finishPhoto(pending, fname)

class PhotoSMTPFactory(smtp.SMTPFactory):
    protocol = smtp.SMTP

    def __init__(self):
        smtp.SMTPFactory.__init__(self)
        self.db = DatabaseRunner()

    def buildProtocol(self, addr):
        p = smtp.SMTPFactory.buildProtocol(self, addr)
        p.delivery = PhotoMessageDelivery()
        p.delivery.db = self.db
        return p

def main():
    from twisted.application import internet
    from twisted.application import service

    a = service.Application("Photo SMTP Server")
    internet.TCPServer(8025, PhotoSMTPFactory()).setServiceParent(a)

    return a

application = main()
