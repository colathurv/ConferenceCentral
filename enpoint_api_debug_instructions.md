I am sharing this set of instructions for debugging google end point APIs 
both locally on your desktop and on the google cloud infrastructure using 
logging statements in python code, 

#What to do in code to add a debug statement ?

In your python module

Do an import 

``` import logging ```

Call the logger 

```
logging.getLogger().setLevel(logging.DEBUG)
```

Write your logging statement 

```
logging.debug("getConferenceSessions:: About to query Conference")
```


# What to setup locally on your desktop in order to see the logs ?

Invoke your desktop version of the Google App Engine Launcher, choose your 
project, then File > Appliction settings, enter the following as per the 
this [snapshot](https://github.com/colathurv/ConferenceCentral/issues/1) and 
then click update. Note that you should stop any running 
projects before doing the update. 

When you run the project you should see the debug statement taking effect
in the command line as [follows](https://github.com/colathurv/ConferenceCentral/issues/2)



# Where to see the local logs and how do these logs look like ?

The logs will appear on your log console and will look like the snapshot
[here](https://github.com/colathurv/ConferenceCentral/issues/3)

Notice the logging statement in the first section of this article
gets surfaced in the logs.


# What to setup on the google cloud in order to see your logs ?

Nothing at all, except deploying your python code with the logging statements
above to google, from the app engine launcher on your desktop.


# Where to see the cloud logs and how do these logs look like ?
Go to your developer console as in this [snapshot](https://github.com/colathurv/ConferenceCentral/issues/4)

You will see the logs as shown [here](https://github.com/colathurv/ConferenceCentral/issues/5)