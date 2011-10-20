from flask import Flask, redirect, url_for, request, g, session, abort
from flaskext.sqlalchemy import SQLAlchemy
from flaskext.uploads import UploadSet, IMAGES, configure_uploads
from flaskext.genshi import Genshi, render_response
from flaskext.openid import OpenID
from flaskext import wtf

from wtforms.ext.dateutil.fields import DateField
from wtforms.ext.sqlalchemy.fields import QuerySelectField
from dateutil.relativedelta import relativedelta
from decorator import decorator
import collections
import calendar
import datetime
import markdown
import genshi

calendar.setfirstweekday(6)
last_day_delta = relativedelta(day=31)

app = Flask(__name__)
app.config.from_envvar('SHURT_SETTINGS')
genshi_wrap = Genshi(app)
genshi_wrap.extensions['html'] = 'html5'

shirt_set = UploadSet('shirts', extensions=IMAGES)
configure_uploads(app, [shirt_set])

db = SQLAlchemy(app)

oid = OpenID(app)

class Kwinit(object):
    def __init__(self, **kwargs):
        for kv in kwargs.iteritems():
            setattr(self, *kv)
        super(Kwinit, self).__init__()

class Editor(db.Model):
    __tablename__ = 'editors'
    openid = db.Column(db.String(), primary_key=True)

class Note(db.Model):
    __tablename__ = 'notes'
    id = db.Column(db.Integer(), primary_key=True)
    when = db.Column(db.DateTime(), nullable=False, default=datetime.datetime.now)
    note = db.Column(db.String(), nullable=False)

    @property
    def formatted(self):
        return genshi.Markup(markdown.markdown(self.note))

shirt_notes_table = db.Table(
    'shirt_notes', db.metadata,
    db.Column('shirt_id', db.Integer(), db.ForeignKey('shirts.id'), primary_key=True),
    db.Column('note_id', db.Integer(), db.ForeignKey('notes.id'), primary_key=True),
)

class Shirt(db.Model):
    __tablename__ = 'shirts'
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(), nullable=False)
    acquired = db.Column(db.Date())
    photo_filename = db.Column(db.String())
    notes = db.relationship(Note, secondary=shirt_notes_table)

wearing_notes_table = db.Table(
    'wearing_notes', db.metadata,
    db.Column('wearing_id', db.Integer(), db.ForeignKey('wearings.id'), primary_key=True),
    db.Column('note_id', db.Integer(), db.ForeignKey('notes.id'), primary_key=True),
)

class Wearing(db.Model):
    __tablename__ = 'wearings'
    id = db.Column(db.Integer(), primary_key=True)
    shirt_id = db.Column(db.Integer(), db.ForeignKey(Shirt.id), nullable=False)
    shirt = db.relationship(Shirt, backref='wearings')
    when = db.Column(db.Date(), nullable=False, index=True)
    specifically_when = db.Column(db.Time())
    notes = db.relationship(Note, secondary=wearing_notes_table)

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
    today = datetime.datetime.now()
    return wearing_calendar(today.month, today.year, today)

@app.route('/<int:year>/<int:month>/')
def wearing_calendar(month, year, today=None):
    first_day = datetime.date(year, month, 1)
    last_day = first_day + last_day_delta
    wearings = Wearing.query.filter(db.between(Wearing.when, first_day, last_day)).all()
    wearing_map = collections.defaultdict(list)
    for wearing in wearings:
        wearing_map[wearing.when.day].append(wearing)
    cal = calendar.monthcalendar(year, month)
    return render_response('calendar.html',
                           dict(calendar=cal, month=month, year=year, today=today, wearing_map=wearing_map))

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
            note = Note(note=form.description.data)
            wearing.notes.append(note)
            db.session.add(note)
        db.session.commit()
        return redirect(url_for('wearing_calendar', month=month, year=year))

    shirts = Shirt.query.all()
    return render_response('wear_on.html', dict(day=day, month=month, year=year, form=form, shirts=shirts))

@app.route('/shirts/')
def shirts():
    shirts = Shirt.query.order_by(Shirt.name).all()
    return render_response('shirts.html', dict(shirts=shirts))

@app.route('/shirts/<id>')
def shirt_detail(id):
    shirt = Shirt.query.get(id)
    if shirt.photo_filename:
        photo_url = shirt_set.url(shirt.photo_filename)
    else:
        photo_url = None
    return render_response('shirt_detail.html', dict(shirt=shirt, photo_url=photo_url))

@app.route('/shirts/edit/<id>')
@needs_login
def shirt_edit(id):
    shirt = Shirt.query.get(id)
    if shirt.photo_filename:
        photo_url = shirt_set.url(shirt.photo_filename)
    else:
        photo_url = None
    return render_response('shirt_detail.html', dict(shirt=shirt, photo_url=photo_url))

class AddForm(wtf.Form):
    name = wtf.TextField(validators=[wtf.required()])
    description = wtf.TextField('Initial notes', widget=wtf.TextArea(), validators=[wtf.optional()])
    acquired = DateField('Acquired on', validators=[wtf.optional()])
    photo = wtf.FileField('Shirt photo', validators=[wtf.optional(), wtf.file_allowed(shirt_set)])

@app.route('/shirts/add', methods=['GET', 'POST'])
@needs_login
def shirt_add():
    form = AddForm()
    if form.validate_on_submit():
        shirt = Shirt(
            name=form.name.data,
            acquired=form.acquired.data,
            photo_filename=shirt_set.save(request.files['photo']) if request.files.get('photo') else None)
        db.session.add(shirt)
        if form.description.data:
            note = Note(note=form.description.data)
            shirt.notes.append(note)
            db.session.add(note)
        db.session.commit()
        return redirect(url_for('shirts'))

    return render_response('shirt_add.html', dict(form=form))
