from flask import Flask, redirect, url_for, request, g, session, abort
from flaskext.sqlalchemy import SQLAlchemy
from flaskext.uploads import UploadSet, IMAGES, configure_uploads
from flaskext.genshi import Genshi, render_response
from flaskext.openid import OpenID
from flaskext import wtf

from wtforms.ext.dateutil.fields import DateField
from wtforms.ext.sqlalchemy.fields import QuerySelectField
from decorator import decorator
import collections
import calendar
import datetime
import markdown
import genshi
import urllib
import uuid

cal = calendar.Calendar(calendar.SUNDAY)

app = Flask(__name__)
app.config.from_envvar('SHURT_SETTINGS')
genshi_wrap = Genshi(app)
genshi_wrap.extensions['html'] = 'html5'

photo_set = UploadSet('photos', extensions=IMAGES)
configure_uploads(app, [photo_set])

db = SQLAlchemy(app)

oid = OpenID(app)

def rel_generator(parent, plural, singular=None):
    singular = singular or parent.__name__.lower()
    def deco(cls):
        lower_child = cls.__name__.lower()
        child_fk = db.Column(db.Integer(), db.ForeignKey(cls.id), primary_key=True)
        generated_type = type(parent)(
            cls.__name__ + parent.__name__,
            (parent,),
            {
                '__tablename__': '%s_%s' % (lower_child, plural),
                '__mapper_args__': dict(polymorphic_identity=lower_child),
                singular + '_id': db.Column(db.Integer(), db.ForeignKey(parent.id), primary_key=True),
                lower_child + '_id': child_fk,
                lower_child: db.relationship(cls, backref=db.backref(plural, cascade='all, delete-orphan'))
            }
        )
        setattr(cls, 'has_' + singular, db.column_property(db.exists(['*']).where(cls.id == child_fk)))
        setattr(cls, parent.__name__, generated_type)
        return cls
    return deco

class Editor(db.Model):
    __tablename__ = 'editors'
    openid = db.Column(db.String(), primary_key=True)

class Note(db.Model):
    __tablename__ = 'notes'
    id = db.Column(db.Integer(), primary_key=True)
    when = db.Column(db.DateTime(), nullable=False, default=datetime.datetime.now)
    note = db.Column(db.String(), nullable=False)
    type = db.Column(db.String())
    __mapper_args__ = dict(polymorphic_on=type)

    @property
    def formatted(self):
        return genshi.Markup(markdown.markdown(self.note))

with_notes = rel_generator(Note, 'notes')

@with_notes
class Photo(db.Model):
    __tablename__ = 'photos'
    id = db.Column(db.Integer(), primary_key=True)
    when = db.Column(db.DateTime(), nullable=False, default=datetime.datetime.now)
    filename = db.Column(db.String(), nullable=False)
    type = db.Column(db.String())
    __mapper_args__ = dict(polymorphic_on=type)

    @property
    def url(self):
        return photo_set.url(self.filename)

    def detail_url(self):
        return url_for('photo_detail', id=self.id)

with_photos = rel_generator(Photo, 'photos')

class PendingPhoto(db.Model):
    __tablename__ = 'pending_photos'
    id = db.Column(db.Integer(), primary_key=True)
    editor_id = db.Column(db.String(), db.ForeignKey(Editor.openid))
    editor = db.relationship(Editor, backref=db.backref('pending_photos', cascade='all, delete-orphan'))
    when = db.Column(db.DateTime(), nullable=False, default=datetime.datetime.now)
    key = db.Column(db.String(), nullable=False)
    type = db.Column(db.String(), nullable=False)
    __mapper_args__ = dict(polymorphic_on=type)

    def generate_key(self):
        self.key = uuid.uuid4().hex

    @property
    def mailto_link(self):
        return 'mailto:%s?subject=%s' % (app.config['NOTES_EMAIL'], urllib.quote(self.key))

with_pending_photos = rel_generator(PendingPhoto, 'pending_photos', singular='pending_photo')

@with_notes
@with_photos
@with_pending_photos
class Shirt(db.Model):
    __tablename__ = 'shirts'
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(), nullable=False)
    acquired = db.Column(db.Date())

    def detail_url(self):
        return url_for('shirt_detail', id=self.id)

@with_notes
@with_photos
@with_pending_photos
class Wearing(db.Model):
    __tablename__ = 'wearings'
    id = db.Column(db.Integer(), primary_key=True)
    shirt_id = db.Column(db.Integer(), db.ForeignKey(Shirt.id), nullable=False)
    shirt = db.relationship(Shirt, backref=db.backref('wearings', cascade='all, delete-orphan'))
    when = db.Column(db.Date(), nullable=False, index=True)
    specifically_when = db.Column(db.Time())

    def detail_url(self):
        return url_for('wearing_detail', id=self.id)

@app.before_request
def lookup_current_user():
    g.user = None
    if 'openid' in session:
        g.user = Editor.query.filter_by(openid=session['openid']).first()

@app.route('/login', methods=['GET', 'POST'])
@oid.loginhandler
def login():
    if g.user is not None:
        return redirect(oid.get_next_url())
    if request.method == 'POST':
        openid = request.form.get('openid')
        if openid:
            return oid.try_login(openid, ask_for=['nickname'])
    return render_response('login.html', dict(next=oid.get_next_url(), error=oid.fetch_error()))

@oid.after_login
def after_login(resp):
    session['openid'] = resp.identity_url
    user = Editor.query.filter_by(openid=resp.identity_url).first()
    if user is None and session.get('allow_creation'):
        user = Editor(openid=resp.identity_url)
        db.session.add(user)
        db.session.commit()
        del session['allow_creation']
    elif user is None:
        abort(403)
    g.user = user
    return redirect(oid.get_next_url())

@app.route('/logout')
def logout():
    session.pop('openid', None)
    return redirect(oid.get_next_url())

@decorator
def needs_login(f, *a, **kw):
    if g.user is None:
        abort(403)
    return f(*a, **kw)

@app.route('/')
def index():
    if not g.user and not Editor.query.first():
        session['allow_creation'] = True
        return redirect(url_for('login'))
    today = datetime.date.today()
    return wearing_calendar(today.month, today.year, today)

@app.route('/<int:year>/<int:month>')
def wearing_calendar(month, year, today=None):
    weeks = cal.monthdatescalendar(year, month)
    first_day, last_day = weeks[0][0], weeks[-1][-1]
    wearings = Wearing.query.filter(db.between(Wearing.when, first_day, last_day)).all()
    wearing_map = collections.defaultdict(list)
    for wearing in wearings:
        wearing_map[wearing.when].append(wearing)
    return render_response('calendar.html',
                           dict(weeks=weeks, month=month, year=year, today=today, wearing_map=wearing_map))

class WearOnForm(wtf.Form):
    shirt = QuerySelectField(query_factory=lambda: Shirt.query.order_by(Shirt.name),
                             get_label='name',
                             validators=[wtf.required()])
    when = wtf.TextField(validators=[wtf.optional()])
    description = wtf.TextField('Initial notes', widget=wtf.TextArea(), validators=[wtf.optional()])

@app.route('/<int:year>/<int:month>/<int:day>/wear', methods=['GET', 'POST'])
@needs_login
def wear_on(day, month, year):
    form = WearOnForm()
    if form.validate_on_submit():
        wearing = Wearing(shirt=form.shirt.data,
                          when=datetime.date(year, month, day))
        db.session.add(wearing)
        if form.description.data:
            note = Wearing.Note(note=form.description.data, wearing=wearing)
            db.session.add(note)
        db.session.commit()
        return redirect(url_for('wearing_calendar', month=month, year=year))

    shirts = Shirt.query.all()
    return render_response('wear_on.html', dict(day=day, month=month, year=year, form=form, shirts=shirts))

class AddNoteForm(wtf.Form):
    note = wtf.TextField('Add a note', widget=wtf.TextArea(), validators=[wtf.required()])
    submit_note = wtf.SubmitField('Note')

@app.route('/photos/<int:id>')
def photo_detail(id):
    photo = Photo.query.get(id)
    return render_response('photo_detail.html', dict(photo=photo, form=AddNoteForm()))

@app.route('/photos/<int:id>/note', methods=['POST'])
@needs_login
def photo_note(id):
    photo = Photo.query.get(id)
    form = AddNoteForm()
    if form.validate_on_submit():
        note = Photo.Note(photo=photo, note=form.note.data)
        db.session.add(note)
        db.session.commit()
        return redirect(url_for('photo_detail', id=id))
    return render_response('photo_detail.html', dict(photo=photo, form=form))

class AddPhotoNoteForm(wtf.Form):
    photo = wtf.FileField('Add a photo', validators=[wtf.optional(), wtf.file_allowed(photo_set)])
    note = wtf.TextField('Add a note', widget=wtf.TextArea())
    submit_note = wtf.SubmitField('Note')
    submit_email = wtf.SubmitField('Photo by e-mail')

    def validate_note(self, field):
        if not field.data and not self['photo'].data and not self['submit_email'].data:
            raise wtf.ValidationError('At least a note or a photo must be specified')

def add_photo_note(form, model, **model_params):
    if request.files.get('photo'):
        photo = model.Photo(filename=photo_set.save(request.files['photo']), **model_params)
        db.session.add(photo)
        if form.note.data:
            note = Photo.Note(note=form.note.data, photo=photo)
            db.session.add(note)
    elif form.note.data:
        note = model.Note(note=form.note.data, **model_params)
        db.session.add(note)
    elif form.submit_email.data:
        pending = model.PendingPhoto(editor=g.user, **model_params)
        pending.generate_key()
        db.session.add(pending)
        session['redirect_uri'] = pending.mailto_link
    else:
        raise ValueError('this should never happen ??')

@app.route('/wearings/<int:id>')
def wearing_detail(id):
    wearing = Wearing.query.get(id)
    return render_response('wearing_detail.html', dict(wearing=wearing, form=AddPhotoNoteForm()))

@app.route('/wearings/<int:id>/note', methods=['POST'])
@needs_login
def wearing_note(id):
    wearing = Wearing.query.get(id)
    form = AddPhotoNoteForm()
    if form.validate_on_submit():
        add_photo_note(form, Wearing, wearing=wearing)
        db.session.commit()
        return redirect(url_for('wearing_detail', id=id))
    return render_response('wearing_detail.html', dict(wearing=wearing, form=form))

@app.route('/shirts')
def shirts():
    shirts = Shirt.query.order_by(Shirt.name).all()
    return render_response('shirts.html', dict(shirts=shirts))

@app.route('/shirts/<int:id>')
def shirt_detail(id):
    shirt = Shirt.query.get(id)
    return render_response('shirt_detail.html', dict(shirt=shirt, form=AddPhotoNoteForm()))

@app.route('/shirts/<int:id>/note', methods=['POST'])
@needs_login
def shirt_note(id):
    shirt = Shirt.query.get(id)
    form = AddPhotoNoteForm()
    if form.validate_on_submit():
        add_photo_note(form, Shirt, shirt=shirt)
        db.session.commit()
        return redirect(url_for('shirt_detail', id=id))
    return render_response('shirt_detail.html', dict(shirt=shirt, form=form))

class EditShirtForm(wtf.Form):
    name = wtf.TextField(validators=[wtf.required()])
    acquired = DateField('Acquired on', validators=[wtf.optional()])

@app.route('/shirts/<int:id>/edit', methods=['GET', 'POST'])
@needs_login
def shirt_edit(id):
    shirt = Shirt.query.get(id)
    form = EditShirtForm(obj=shirt)
    if form.validate_on_submit():
        form.populate_obj(shirt)
        db.session.commit()
    return render_response('shirt_edit.html', dict(form=form, shirt=shirt))

class AddShirtForm(EditShirtForm):
    description = wtf.TextField('Initial shirt notes', widget=wtf.TextArea(), validators=[wtf.optional()])
    photo = wtf.FileField('Shirt photo', validators=[wtf.optional(), wtf.file_allowed(photo_set)])
    also_wear = wtf.BooleanField('Wearing today')

@app.route('/shirts/add', methods=['GET', 'POST'])
@needs_login
def shirt_add():
    form = AddShirtForm()
    if form.validate_on_submit():
        shirt = Shirt(
            name=form.name.data,
            acquired=form.acquired.data)
        db.session.add(shirt)
        if request.files.get('photo'):
            photo = Shirt.Photo(filename=photo_set.save(request.files['photo']), shirt=shirt)
            db.session.add(photo)
        if form.description.data:
            note = Shirt.Note(note=form.description.data, shirt=shirt)
            db.session.add(note)
        if form.also_wear.data:
            wearing = Wearing(shirt=shirt, when=datetime.date.today())
            db.session.add(wearing)
        db.session.commit()
        return redirect(url_for('shirts'))

    return render_response('shirt_add.html', dict(form=form))

class DeleteForm(wtf.Form):
    submit_delete = wtf.SubmitField('Confirm deletion')

@app.route('/shirts/<int:id>/delete', methods=['GET', 'POST'])
@needs_login
def shirt_delete(id):
    shirt = Shirt.query.get(id)
    form = DeleteForm()
    if form.validate_on_submit():
        db.session.delete(shirt)
        db.session.commit()
        return redirect(url_for('shirts'))
    return render_response('confirm_delete.html', dict(form=form, type='shirt', obj=shirt))

@app.route('/wearings/<int:id>/delete', methods=['GET', 'POST'])
@needs_login
def wearing_delete(id):
    wearing = Wearing.query.get(id)
    form = DeleteForm()
    if form.validate_on_submit():
        db.session.delete(wearing)
        db.session.commit()
        return redirect(url_for('index'))
    return render_response('confirm_delete.html', dict(form=form, type='wearing', obj=wearing))

@app.route('/photos/<int:id>/delete', methods=['GET', 'POST'])
@needs_login
def photo_delete(id):
    photo = Photo.query.get(id)
    form = DeleteForm()
    if form.validate_on_submit():
        db.session.delete(photo)
        db.session.commit()
        return redirect(url_for('index'))
    return render_response('confirm_delete.html', dict(form=form, type='photo', obj=photo))
