#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
from datetime import time

import logging
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.ext.db import stats

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionType


from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

logging.getLogger().setLevel(logging.DEBUG)

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_SPEAKER_KEY = "FEATURED SPEAKER"
FEATURED_SPEAKER_ANNOUNCEMENT_TPL = ('Featured speaker for this conference is %s.'
                    ' Please plan on attending these sessions!')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}
SESSION_DEFAULTS = {
    'duration': 50,
}
OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_CONF_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_WISHLIST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    typeOfSession=messages.StringField(1),
    websafeConferenceKey=messages.StringField(2),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

# - - - Session objects - - - - - - - - - - - - - - - - - - -

########## TASK 1 :: createSession ##########
    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm"""

        # create a new entity first
        sf = SessionForm()

        # convert Session properties to SessionForm fields
        sf.sessionName = session.sessionName
        sf.highlights = session.highlights
        sf.speaker = session.speaker
        sf.duration = session.duration
        #logging.debug("copySession :: About to set typeOfSession")
        #logging.debug("copySession :: type of Session type is %s", type(session.typeOfSession))
        sf.typeOfSession = getattr(SessionType,session.typeOfSession)
        sf.sessionDate = str(session.sessionDate)
        sf.startTime = int('%s%s' % (str(session.startTime)[:2], str(session.startTime)[3:5]))
        sf.websafeKey = session.key.urlsafe()

        # check and return
        sf.check_initialized()
        return sf


    @endpoints.method(SESSION_POST_CONF_REQUEST, SessionForm,
            path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session for a given conference"""

        # Make sure user is authorized
	user = endpoints.get_current_user()
	if not user:
	    raise endpoints.UnauthorizedException('Authorization is required')
        user_id = getUserId(user)
        
        # Validate required fields
        if not request.sessionName:
            raise endpoints.BadRequestException("Session Name field needs to be entered")
        if not request.sessionDate:
            raise endpoints.BadRequestException("Session Date field needs to be entered. Use the YYYY-MM-DD format, for example 2016-01-17.")
        if not request.startTime:
            raise endpoints.BadRequestException("Session Start Time field needs to be entered. Use the HHMM format, for example 0830")
        if not request.speaker:
            raise endpoints.BadRequestException("A Session needs to have a Speaker") 
        if not request.typeOfSession:
            raise endpoints.BadRequestException("A type of Session needs to be entered. For example WORKSHOP or KEYNOTE etc.") 
            
        # get conference from websafeconference key in a try catch block
	try:
	    conf = ndb.Key(urlsafe=request.websafeConferenceKey).get() 
	        
	except:
            raise endpoints.BadRequestException('No conference found with key: %s' % request.websafeConferenceKey)
            
            
        # Validate that user is also the organizer of the conference
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException('Only creator of the conference can add sessions to it')

        # Create the data object as a dictionary and populate it
        data = {}
        data['sessionName'] = request.sessionName
        data['highlights'] = request.highlights
        data['speaker'] = request.speaker
        data['duration'] = request.duration
        data['typeOfSession'] = request.typeOfSession.name
        try:
            data['sessionDate'] = datetime.strptime(request.sessionDate[:10], '%Y-%m-%d').date()
        except:
            raise endpoints.BadRequestException('Make sure your Session Date is in the format YYYY-MM-DD. For example 2016-01-17.')
        try:    
            data['startTime'] = datetime.strptime(str(request.startTime)[:4], '%H%M').time()
        except:
            raise endpoints.BadRequestException('Make sure your Start Time is in the format HHMM. For example 0830')
        
        # Generate Session Key based on the conference key
        p_key = ndb.Key(urlsafe=request.websafeConferenceKey)
	s_id = Session.allocate_ids(size=1, parent=p_key)[0]
	s_key = ndb.Key(Session, s_id, parent=p_key)
	data['key'] = s_key       

############# TASK 4 ::  Creating a Task Queue for capturing a speaker that speaks more than once in a Conference [aka Featured Speaker] #############

        # When a new session is created, kick off a task which caches speakers that participate in more than one Session.
        
        #logging.debug("createSession :: About to add taskqueue with websafeconference %s",request.websafeConferenceKey )
        
        taskqueue.add(
            params={
                'websafeConferenceKey': request.websafeConferenceKey,
                'speaker': data['speaker']
                },
            url='/tasks/cache_featured_speaker'
            )

        
        #logging.debug("createSession :: About to insert into session")

        # Get the session object into the datastore
        session = Session(**data)
        session.put()

        # return SessionForm
        return self._copySessionToForm(session)        
     
 
  
########## TASK 1 :: getConferenceSessions        #############

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='session/{websafeConferenceKey}',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all sessions based on a websafeConferenceKey"""
        #logging.debug("getConferenceSessions:: About to query Conference")
        # try and catch conferences that do not exist
        try:
            conf = ndb.Key(urlsafe=request.websafeConferenceKey).get() 
        except:
            raise endpoints.BadRequestException('Conference not found for key: %s' % request.websafeConferenceKey)
        
        # Ancestor query
        sessions = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))
        #logging.debug("getConferenceSessions:: Ancestor queried Successfully")
        #for sess in sessions:
            #logging.debug("Session Name is %s", sess.sessionName) 
        
        # return set of ConferenceForm objects per Conference
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )

############# TASK 1 ::  getSessionsBySpeaker      #############

    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms,
            path='session/speaker/{speaker}',
            http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return sessions where the speaker is the one passed in the request."""
        
        # Query Session based on Speaker
        sessions = Session.query(Session.speaker == request.speaker)
        
        # SessionForm objects per session
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )

############# TASK 1 :: getConferenceSessionsByType  #############

    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms,
            path='session/{websafeConferenceKey}/SessionType/{typeOfSession}',
            http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return all sessions of the type that belong to the conference passed in the request."""
        
        # try and catch conferences that do not exist
        try:
            conf = ndb.Key(urlsafe=request.websafeConferenceKey).get() 
        except:
            raise endpoints.BadRequestException('Conference not found for key: %s' % request.websafeConferenceKey)
        
        # Ancestor query
        q = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))
        sessions = q.filter(Session.typeOfSession == request.typeOfSession)
        
        
 
        # Session Form Objects per session
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )

############# TASK 2 ::  addSessionToWishlist #############

    @ndb.transactional(xg=True)
    def _addSessionToWishlist(self, request):
        """Adds the session to the user's list of sessions they are interested in attending."""
       
        # Get Current User
        user = endpoints.get_current_user()
	if not user:
	    raise endpoints.UnauthorizedException('Authorization is required')
	
        # Get profile of user from Profile datastore
	user_id = getUserId(user)
	p_key = ndb.Key(Profile, user_id)
	profile = p_key.get()
	
	# Create new Profile if it does not exist already
	if not profile:
	    profile = Profile(
	                key = p_key,
	                displayName = user.nickname(),
	                mainEmail= user.email(),
	                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
	            )
	    profile.put()

        # Get websafesession key from request and raise hell if it does not resolve to a valid session       
        ses_key = request.websafeSessionKey      
        try:
            sess = ndb.Key(urlsafe=ses_key).get() 
        except:
            raise endpoints.BadRequestException('No session found with key: %s' % ses_key)
        
        
        # Check if user is already added session to Wishlist 
        if ses_key in profile.SessionsInWishlist:
            raise ConflictException(
                  "This Session has been already added to WishList")

        # Add to WishList
        profile.SessionsInWishlist.append(ses_key)
        retval = True

        # Write to Profile datastore & return
        profile.put()
        return BooleanMessage(data=True)


    @endpoints.method(SESSION_POST_WISHLIST_REQUEST, BooleanMessage,
            path='wishlist/add/{websafeSessionKey}',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add Session to WishList."""
        return self._addSessionToWishlist(request)

############### TASK 2 ::  getSessionsInWishlist   ################

    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='sessions/wishlist',
            http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Query for all sessions in a conference that the user is interested in."""
        
        # Get current user
        user = endpoints.get_current_user()
	if not user:
	    raise endpoints.UnauthorizedException('Authorization is required')
	
        # Get profile of user from Profile datastore
	user_id = getUserId(user)
	p_key = ndb.Key(Profile, user_id)
	profile = p_key.get()
	
	# Create new Profile if it does not exist already
	if not profile:
	    profile = Profile(
	                key = p_key,
	                displayName = user.nickname(),
	                mainEmail= user.email(),
	                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
	            )
	    profile.put()
        
        sess_keys = [ndb.Key(urlsafe=ses) for ses in profile.SessionsInWishlist]
        sessions = ndb.get_multi(sess_keys)

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )
        
        
#############  TASK 3 ::  All non-workshop sessions before 7 pm   ################
    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='sessions/before7filter',
            http_method='GET', name='getNWSessionsBefore7')
    def getNWSessionsBefore7(self, request):
        """Return all non-workshop sessions before 7 pm"""

        # First get all non-workshop sessions
        q = Session.query()
        q = q.filter(Session.typeOfSession!='WORKSHOP')

        # Loop over Sessions returned above to return only 
        # sessions whose start time is before 7pm
        sess_list = []
        for s in q:
            strStartTime = str(s.startTime)
            if strStartTime != 'None':
                if int(s.startTime.hour) < 19:
                    sess_list.append(s)

        # return set of SessionForm objects per Items
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sess_list]
        )

#############  TASK 3 ::  getTotalNumberOfSessions  ################

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='sessions/totalSessions',
            http_method='GET', name='getTotalNumberOfSessions')
    def getTotalNumberOfSessions(self, request):
        """Get the Total Number of Sessions"""

        # Query sessions and get the SUM
        qCount = Session.query().count()
        #logging.debug("getTotalNumberOfSessions :: Count is %d", qCount)

        # Return SessionForm object
        return StringMessage(data=str(qCount) or "")


#############  TASK 3 ::  Get all Keynote Speakers  ################
    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='sessions/keynoteSpeakers',
            http_method='GET', name='getKeynoteSpeakers')
    def getKeynoteSpeakers(self, request):
        """Get a list of Keynote speakers"""

        # Get all sessions of type KEYNOTE
        q = Session.query().filter(Session.typeOfSession=='KEYNOTE')

        # Assign all returned speakers to a speaker array
        speaker_arr = []
        for i in q:
            speaker_arr.append(str(i.speaker))

        # Assign values to a set and generate string
        unique_res = set(speaker_arr)    
        speaker_list = ', ' .  join(unique_res)

        # return String of comma separated speakers
        return StringMessage(data=speaker_list or "")

##################### TASK 4 :: The function that the featuredSpeaker Task would call #############
    @staticmethod
    def _cacheFeaturedSpeaker(self):
        """Add featured speaker to memcache.
        """     
        #logging.debug("_cacheFeaturedSpeaker ::  Begin")
        
        speaker = self.request.get('speaker')
        confkey = self.request.get('websafeConferenceKey')
        
        #logging.debug("_cacheFeaturedSpeaker ::  speaker is %s", speaker)
        #logging.debug("_cacheFeaturedSpeaker ::  websafeconference key is %s", confkey)

        # get conference from websafeconference key in a try catch block
        try:
            conf = ndb.Key(urlsafe=confkey).get() 
        except:
            raise endpoints.BadRequestException('No conference found with key: %s' % request.websafeConferenceKey)
        
        # create ancestor query for all session entities of the given conference
        q = Session.query(ancestor=ndb.Key(urlsafe=confkey))
 
        # Get Count of sessions in which speaker speaks
        speakerCount = q.filter(Session.speaker == speaker).count()
     
        #logging.debug("_cacheFeaturedSpeaker ::  speakerCount is %s", speakerCount)
        
        # If speaker is a featured speaker add the Announcement to MEMCACHE    
        if speakerCount > 1:
            #logging.debug("_cacheFeaturedSpeaker :: Adding speaker %s to memcache ", str(speaker) )
            Announcement = FEATURED_SPEAKER_ANNOUNCEMENT_TPL % str(speaker)
            memcache.set(MEMCACHE_SPEAKER_KEY, Announcement)


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='featuredSpeaker/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_SPEAKER_KEY) or "")

api = endpoints.api_server([ConferenceApi]) # register API