# Project
This is created for completing Project 4 of the Udacity Full Stack Web Developer Course, where the specification is to extend
an existing and working conference app to support all the following endpoint APIs using the Google App Engine 
and explain design choices where necessary.

+ getConferenceSessions(websafeConferenceKey) - Given a conference, return all sessions
+ getConferenceSessionsByType(websafeConferenceKey, typeOfSession) 
        - Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop)
+ getSessionsBySpeaker(speaker)
        - Given a speaker, return all sessions given by this particular speaker, across all conferences
+ createSession(SessionForm, websafeConferenceKey)
        - Open only to the organizer of the conference
+ addSessionToWishlist(SessionKey) 
        - Adds the session to the user's list of sessions they are interested in attending
+ getSessionsInWishlist()
        - Query and return all the sessions in a conference that the user is interested in
+ getFeaturedSpeaker()
        - Using the Google App Engine's Task Queue, cache speakers that speak in more than one session
        in Memcache and return these speakers [aka featured speakers], from memcache.

The following APIs are implemented based on the specification to implement 3 extra queries
+ getNWSessionsBefore7()
        - Returns all sessions which start before 7:00 PM
+ getKeynoteSpeakers()
        - Returns all Speakers who speak in atleast one keynote
+ getTotalNumberOfSessions()
        - Returns the total number of sessions.


# Artifacts
While I have checked in the entire Conference Central directory the files I made changes to are only the following. The rest of them are as in the 
directory ud858 shared by Udacity -

+ app.yaml - Configuration file which contains my application details such as the project id and the declaration
             of the task queue that gets invoked from the createSession API above [through a Handler API] for the 
             purpose of caching the featured speaker.
+ conference.py - Python module that contains the implementations of all the above APIs.
+ main.py - Contains the logic for the Handler that calls the caching logic for the featured speaker.
+ models.py - Contains the model information of the new Kinds, Forms and Enums introduced to support all the above APIs.
+ settings.py - Contains the Client ID I created and used to test this project.

# Application Details

The End Point APIs have been tested both [locally](http://localhost:8080/_ah/api/explorer) and using the Google Developer [Console](https://civic-environs-100223.appspot.com/_ah/api/explorer) 


# Design Choices

At the outset, logging APIs using the logging module was of great use to me through out. 
I have included a separate README for configuring logging, as a FYI to this project. This 
is in enpoint_api_debug_instructions.md. 

The are my reasons for implementing the way I did and some of the reasons for the choices.

+ Task 1 (Add Sessions to Conference)

  I chose to keep Session as the child of a Conference, since it does not make sense any other way. I made Speaker
  a property of Session, with out having a full blown kind for it, for the sake of simplicity. I made the duration
  as a default with 50 minutes and made SessionName, Sesion Type, Speaker, Start Time and Session Date as mandatory fields. I also
  implemented Validation Messages for all the 5 mandatory fields and specifying the desired format for the Date and Start Time fields.
  The start time is implemented as an Integer Field in the UI form, with the conversion from Integer to TimeProperty and vice versa
  happening in the API. 
  
+ Task 2 (Add Sessions to User Wishlist)

  I included a websafekey for the Sessionform, so that Sessions are with their websafekey 
  so that, I can use them to add to my Wishlist. When a same session is added multiple times I return a validation message.

  
+ Task 3 (Work on Indexes and Queries) 

  First and foremost I decided to not over-ride the default of autogenerated indexes on index.yaml. I incorporated the following 
  2 new queries -
  
  getKeynoteSpeakers() - Returns all Speakers who speak in atleast one keynote. 
  getTotalNumberOfSessions - Returns the total number of Sessions across all conferences. While I understand that the Datastore Stats APIs
  are the best way to do aggregation on Kinds(for all large volumes and consistency), I resorted to a simple way of implementing them.
  getNWSessionsBefore7() - 
  
  
  I first tried both of the following unsuccessfully -
  
  ```q = q.filter( int(str(Session.startTime.hour)) < 19 )```
  
  ```AttributeError: 'TimeProperty' object has no attribute 'hour'```
    
  ```q = q.filter( int(str(Session.startTime)[:2]) < 19 )```
  
  ```ValueError: invalid literal for int() with base 10: 'Ti'```

  ```q = Session.query(ndb.AND(Session.typeOfSession !='WORKSHOP', int(str(Session.startTime)[:2]) < 19 ))```
  
  ```ValueError: invalid literal for int() with base 10: 'Ti'```
  
  I learnt the hardway that the Google Apps Engine has a restriction(see links [1](https://cloud.google.com/appengine/docs/python/datastore/queries?hl=en#Python_Restrictions_on_queries) and [2](https://cloud.google.com/appengine/docs/java/datastore/queries?csw=1#Java_Restrictions_on_queries)), where in an NDB query cannot have more than 1 property that has an inequality filter, due to performance considerations where in a query's potential results cannot be adjacent to each other, without this restriction. Since the query for this task requires more than 1 property that requires an Inequality filter, we cannot
  solve this with an NDB query. It would require an iteration of results and then a filter applied to each item in the list.
  So I went with the following approach.
  
  Iterate through the Session query with a inequality filter on Workshop and from each session in the loop only 
  add the sessions with a start time (before 7) by an inequality filter using simple python (not an NDB query) and populate
  another list. Return this list at the end.
  
  In any case, GAE's Value error messages did not seem intuitive at all. Couldn't GAE returned right away that more than one Inequality filter is 
  not supported, I wonder.   

  
+ Task 4 (Add a Task Queue)   

  I incorporated a static method called _cacheFeaturedSpeaker that added a speaker to memcache, based on the session count of 
  the speaker's session. I invoke this using a task queue that is part of the createSession API,whic gets called through a handler
  called CacheFeaturedSpeakerHandler in main.py.
  
  The API getFeaturedSpeaker, just returns the featured speaker from memcache. 