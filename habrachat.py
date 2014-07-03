#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import sys

import six

import tornado.web
import tornado.websocket
import tornado.ioloop
import tornado.httpserver
import tornado.options
import tornado.auth
from tornado.escape import utf8,  recursive_unicode
from tornado import template
from tornado.options import define, options
from tornado import  gen, httpclient

import json
import hashlib

from base64 import b64encode
from tornado.escape import json_decode, xhtml_escape
#import bbcode
from postmarkup import render_bbcode
import datetime
import time
import dateutil.parser
from pytz import timezone

current_zone = timezone('UTC')

import tornadoredis
import logging

log = logging.getLogger("tornado.general")
define("template_root", default="./", help="Root for Template")
define("static_root", default="./static", help="Root for static files")
define("auth_url", default="http://127.0.0.1:8888/auth", help="URL for ulogin auth")
define("moderators", default=[], help="Moderators list")
define("max_save_messages", default=1499, help="Max save messages in redis")
define("max_start_messages", default=149, help="Max messages for send after init socket")

define("ping_every", default=0, help="Ping clients every some time. 0 not ping.")

define("hubs", default=[], help="List of hubs")
define("timezone", default='UTC', help="Server timezone")
define("hostname", default="localhost", help="Server host")
define("subprocess", default=1, help="Num of subprocess")
define("google_oauth", default={"key":"", "secret":""}, help="google oauth")

define("redis_password", default=None, help="Password for redis")
define("redis_port", default=6379, help="Port for redis")
define("redis_host", default="localhost", help="Host for redis")


mp_users = dict() #Users for this instans
mp_cookies = dict()
mp_hubs = dict()
templates = dict()
remote_users = dict()
ban_list = ["b9197a5778203a79f0086b3a0e68e956"]

try:
	import uuid

	def _session_id():
		return uuid.uuid4().hex
except ImportError:
	import random
	if hasattr(os, 'getpid'):
		getpid = os.getpid
	else:
		def getpid():
			return ''

	def _session_id():
		id_str = "%f%s%f%s" % (
			time.time(),
			id({}),
			random.random(),
			getpid()
		)
		# NB: nothing against second parameter to b64encode, but it seems
		#     to be slower than simple chained replacement
		raw_id = b64encode(hashlib.sha1(id_str).digest())
		return raw_id.replace('+', '-').replace('/', '_').rstrip('=')

def json_encode(value, default=None):
	return json.dumps(value, default=default).replace("</", "<\\/")


def have_remote_users(id, hub):
	for user in six.itervalues(remote_users):
		 if user["id"] == id and user["hub"] == hub:
		 	return True
	return False

def have_local_users(id, hub):
	for user in six.itervalues(mp_users):
		 if user["id"] == id and user["hub"] == hub:
		 	return True
	return False

class BaseHandler(object):
	@property
	def redis(self):
		return self.settings['redis']

class ChatHandler(tornado.websocket.WebSocketHandler, BaseHandler):
	def __init__(self, *args, **kwargs):
		super(ChatHandler, self).__init__(*args, **kwargs)
		self.subscriber = Subscriber(self.redis)

	@gen.coroutine
	def open(self):
		habrachat_cookie = self.get_cookie("habrachat")
		if not habrachat_cookie:
			log.info("Not have cookie")
			self.close()
			return

		#log.info("Have cookie %s"%habrachat_cookie)
		habrachat_user = yield tornado.gen.Task(self.redis.get, habrachat_cookie)
		if habrachat_user:
			hub = self.get_argument("hub", options.hubs[0]["name"])
			#log.info("Have user")
			habrachat_user = json_decode(habrachat_user)
			if habrachat_user["id"] in ban_list:
				log.info("Ban user")
				self.close()
				return

			habrachat_user_settings = yield tornado.gen.Task(self.redis.get, "setting_"+habrachat_user["id"])
			if  habrachat_user_settings:
				habrachat_user_settings = json_decode(habrachat_user_settings)
			else:
				habrachat_user_settings = {
					"revert_chat_order": False,
					"send_message_enter": False
				}

			habrachat_user["settings"] = habrachat_user_settings

			habrachat_user["last_event_time"] = datetime.datetime.now(current_zone).strftime("%Y-%m-%dT%H:%M:%S%z")
			habrachat_user["hub"] = hub
			habrachat_user["session_id"] = _session_id()
			habrachat_user["name"] = xhtml_escape(habrachat_user["name"])

			new_user_message = json_encode({
				"type": "new_user",
				"instance_id": self.subscriber.instance_id,
				"hub": hub, #dublicate for simple
				"user": habrachat_user
			})
			#log.info("User id:%s"%habrachat_user["id"] )
			#Checks for the user in the chat
			my_realnew_user = [user for user in six.itervalues(mp_users) if habrachat_user["id"] == user["id"] and hub == user["hub"]]
			#log.info(remote_users)

			if not my_realnew_user:
				my_realnew_user = have_remote_users(habrachat_user["id"], habrachat_user["hub"])

			if not my_realnew_user: #Send about new user to all users
				for sockets, user in six.iteritems(mp_users):
					if habrachat_user["id"] != user["id"] and hub == user["hub"]:
						sockets.write_message(new_user_message)
			
			#Send new users to other instance
			yield tornado.gen.Task(
				self.redis.publish, 
				"new_messages", 
				new_user_message
			)

			log.info("%s WebSocket opened by %s for hub:%s session_id:%s"%(self.subscriber.instance_id, habrachat_user["name"], hub, habrachat_user["session_id"]))
			
			mp_users[self] = habrachat_user
			mp_cookies[self] = habrachat_cookie
			
			#Create uniq user list
			uniq_users = dict()
			for user in six.itervalues(mp_users):
				if hub==user["hub"]:
					uniq_users[user["id"]] = dict(user)
					if user["id"]==habrachat_user["id"]:
						uniq_users[user["id"]]["iam"] = True
			#and from remote users
			for user in six.itervalues(remote_users):
				if hub==user["hub"] and user["id"] not in uniq_users:
					uniq_users[user["id"]] = dict(user)


			#Send all uniq user to new user			
			self.write_message({"type":"all_users", "users":list(uniq_users.values())})
			#Send all hubs
			mp_hubs[hub]["users"] = len(uniq_users)
			self.write_message({"type":"all_hubs", "hubs":list(mp_hubs.values())})
			#Send all last messages
			last_messages = yield tornado.gen.Task(self.redis.lrange, "hub_"+hub, 0, options.max_start_messages)
			self.write_message({"type":"last_messages", "messages":[
				json_decode(message) for message in last_messages
			]})
		else:
			log.info("Not login user")
			self.close()
		
		
	@gen.coroutine
	def on_message(self, message):
		message = json_decode(message)
		if message["type"] == "new_message":
			my_user = mp_users[self]
			if my_user["id"] in ban_list:
				log.info("Ban user")
				self.close()
				return
			time_now = datetime.datetime.now(current_zone)
			last_event_time = dateutil.parser.parse(my_user["last_event_time"])
			#log.error(time_now-last_event_time)
			my_user["last_event_time"] = time_now.strftime("%Y-%m-%dT%H:%M:%S%z")
			if time_now-last_event_time < datetime.timedelta(seconds=5):
				return

			#new_message_text = xhtml_escape(message["message"])
			new_message_text = render_bbcode(message["message"])
			if len(new_message_text) > 2000:
				return

			if new_message_text.strip() == "/logout":
				self.write_message(json_encode({"type": "logout"}))
				return

			#log.info("Start send new message")
			pipe = self.redis.pipeline()

			pipe.lpush("hub_"+my_user["hub"], json_encode({
				"user_id":my_user["id"],
				"datetime":my_user["last_event_time"],
				"text": new_message_text,
				"user": {
					"id":my_user["id"],
					"name": my_user["name"],
					"avatar":my_user["avatar"]
				}
			}))
			pipe.ltrim("hub_"+my_user["hub"], 0, options.max_save_messages)
			response = yield tornado.gen.Task(pipe.execute) # Save message to Redis
			if response[1] != "OK":
				log.error(response)
						
			new_message = json_encode({
				"type": "new_message", 
				"instance_id": self.subscriber.instance_id,
				"hub": my_user["hub"],
				"message": {
					"user": {
						"id": my_user["id"],
						"name": my_user["name"],
						"avatar": my_user["avatar"]
					},
					"text": new_message_text,
					"datetime": my_user["last_event_time"]
				}
			})
			
			for sockets, user in six.iteritems(mp_users):
				if my_user["hub"] == user["hub"]:
					sockets.write_message(new_message)

			#Send new message to other instance
			yield tornado.gen.Task(self.redis.publish, "new_messages", new_message)

		elif message["type"] == "delete_message":
			my_user = mp_users[self]
			
			if my_user["ismoderator"]:
				message_for_delete = None
				redis_messages = yield tornado.gen.Task(self.redis.lrange, "hub_"+my_user["hub"], 0, options.max_save_messages)
				for raw_message in redis_messages:
					decode_message = json_decode(raw_message)
					#print decode_message["user_id"] , decode_message["datetime"] , message["user_id"] , message["datetime"]
					if decode_message["user_id"] == message["user_id"] and decode_message["datetime"] == message["datetime"]:
						message_for_delete = raw_message
						break

				if message_for_delete:
					#log.info(message_for_delete)
					yield tornado.gen.Task(self.redis.lrem, key="hub_"+my_user["hub"],  num=-1, value=message_for_delete)
				else:
					log.warning("Message for delete not found!")
				
				new_message = json_encode({
					"type": "delete_message", 
					"instance_id": self.subscriber.instance_id,
					"hub": my_user["hub"],
					"user_id": message["user_id"], 
					"datetime": message["datetime"]
				})
				for sockets, user in six.iteritems(mp_users):
					if my_user["hub"] == user["hub"]:
						sockets.write_message(new_message)

				#Send about delete to other instance
				yield tornado.gen.Task(self.redis.publish, "new_messages", new_message)
			else:
				log.warning("Try delete message by simple user")
		#elif message["type"] == "active_chat_window":
		#	my_user = mp_users[self]
		#	my_user["last_event_time"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
		#	for sockets, user in mp_users.items():
		#		sockets.write_message(json_encode({"type":"active_chat_window", "user_id":my_user["id"]}))
		elif message["type"] == "all_hubs":
			self.write_message({"type":"all_hubs", "hubs":[_hub for _hub in six.itervalues(mp_hubs)]})
		elif message["type"] == "settings":
			my_user = mp_users[self]
			settings = message.get("settings", {})
			if settings.get("revert_chat_order") != None:
				my_user["settings"]["revert_chat_order"] = settings.get("revert_chat_order", False)
			if settings.get("send_message_enter") != None:
				my_user["settings"]["send_message_enter"] = settings.get("send_message_enter", False)

			yield tornado.gen.Task(self.redis.set, "setting_"+my_user["id"],  json_encode(my_user["settings"]))
	
	@gen.coroutine
	def on_close(self):
		if self in mp_users:
			log.info("WebSocket closed by %s"%mp_users[self]["name"])
			my_id = mp_users[self]["id"]
			hub = mp_users[self]["hub"]
			session_id = mp_users[self]["session_id"]
			del mp_users[self]
			del mp_cookies[self]
			
			my_realnew_user = [user for sockets, user in mp_users.items() if my_id == user["id"] and hub == user["hub"]]
			if not my_realnew_user:
				my_realnew_user = have_remote_users(my_id, hub)
			
			if not my_realnew_user:
				mp_hubs[hub]["users"] -= 1

			new_message = json_encode({
				"type":"del_user", 
				"instance_id": self.subscriber.instance_id,
				"hub": hub,
				"user_id": my_id,
				"session_id": session_id
			})
			if not my_realnew_user:
				for sockets, user in mp_users.items():
					if my_id != user["id"] and hub == user["hub"]:
						sockets.write_message(new_message)

			yield tornado.gen.Task(self.redis.publish, "new_messages", new_message)
		else:
			log.warning("Not found user after close socket")


			

class AuthHandler(tornado.web.RequestHandler, BaseHandler, tornado.auth.GoogleOAuth2Mixin):
	@gen.coroutine
	def post(self):
		habrachat_cookie = self.get_cookie("habrachat")
		if not habrachat_cookie:
			habrachat_cookie = _session_id()
			self.set_cookie("habrachat", habrachat_cookie)

		token = self.get_argument("token", None)
		if not token:
			log.warning("Not have Token")
			self.finish()
			return
		client = httpclient.AsyncHTTPClient()
		response = yield client.fetch(
			"http://u-login.com/token.php?token=%s&host=%s://%s" % (token, self.request.protocol, self.request.host), 
			use_gzip=True
		)
		if response.code != 200:
			log.warning("Not have access to u-login")
			self.finish()
			return

		json_response = json_decode(response.body)
		if "error_type" in json_response:
			log.warning("Error auth: %s" % json_response["error_message"])
			self.finish()
			return

		json_response = json_decode(response.body)
		if "error" in json_response:
			log.warning("Error auth: %s" % json_response["error"])
			self.finish()
			return

		identity = json_response.get("identity")
		if not identity:
			log.error("Not have indentity! json: %s"%json_response)
		log.info("New user indetity: %s"%identity)
		user_id = hashlib.md5(utf8(identity)).hexdigest()
		new_user = {"id": user_id, "name": None}
		
		new_user_name = ""
		if "nickname" in json_response:
			new_user_name = json_response.get("nickname", "")
		if not new_user["name"] and "first_name" in json_response:
			new_user_name = json_response.get("first_name", "")

		new_user["name"] = new_user_name[:20].replace("[", "{").replace("]", "}").encode('UTF-8')

		new_user["avatar"] = json_response.get("photo")
		new_user["ismoderator"] = identity in options.moderators
		
		old_user_settings = yield tornado.gen.Task(self.redis.get, "setting_"+user_id)
		if not old_user_settings:
			new_user_settings = {
				"revert_chat_order": False,
				"send_message_enter": False
			}
			yield tornado.gen.Task(self.redis.set, "setting_"+user_id,  json_encode(recursive_unicode(new_user_settings)))
		yield tornado.gen.Task(self.redis.set, habrachat_cookie,  json_encode(recursive_unicode(new_user)))
		self.redirect("/")

	


class GoogleLoginHandler(tornado.web.RequestHandler, BaseHandler, tornado.auth.GoogleOAuth2Mixin):
	@tornado.gen.coroutine
	def get(self):
		if self.get_argument('code', False):
			token = yield self.get_authenticated_user(
				redirect_uri='%s://%s/google_auth'%(self.request.protocol, self.request.host),
				code=self.get_argument('code'))

			client = httpclient.AsyncHTTPClient()
			response = yield client.fetch(
				"https://www.googleapis.com/oauth2/v1/userinfo?alt=json&access_token=%s" % (token["access_token"]), 
				use_gzip=True
			)
			if response.code != 200:
				log.warning("Not have access to google")
				self.finish()
				return

			user = json_decode(response.body)

			habrachat_cookie = self.get_cookie("habrachat")
			if not habrachat_cookie:
				habrachat_cookie = _session_id()
				self.set_cookie("habrachat", habrachat_cookie)
			identity = user.get("link")
			if not identity:
				log.error("Not have indentity! json: %s"%user)
			log.info("New user indetity: %s"%identity)
			user_id = hashlib.md5(utf8(identity)).hexdigest()
			new_user = {"id": user_id, "name": None}
			if "username" in user:
				new_user["name"] = user.get("username").encode('UTF-8')
			if not new_user["name"] and "name" in user:
				new_user["name"] = user.get("name").encode('UTF-8')

			new_user["name"] = new_user["name"][:20].replace("[", "{").replace("]", "}")
			new_user["avatar"] = user.get("picture", "")
			new_user["ismoderator"] = identity in options.moderators
			
			old_user_settings = yield tornado.gen.Task(self.redis.get, "setting_"+user_id)
			if not old_user_settings:
				new_user_settings = {
					"revert_chat_order": False,
					"send_message_enter": False
				}
				yield tornado.gen.Task(self.redis.set, "setting_"+user_id,  json_encode(recursive_unicode(new_user_settings)))

			yield tornado.gen.Task(self.redis.set, habrachat_cookie,  json_encode(recursive_unicode(new_user)))
			self.redirect("/")
		else:
			yield self.authorize_redirect(
				redirect_uri='%s://%s/google_auth'%(self.request.protocol, self.request.host),
				client_id=self.settings['google_oauth']['key'],
				scope=['profile', 'email'],
				response_type='code',
				extra_params={'approval_prompt': 'auto'})

class MainHandler(tornado.web.RequestHandler, BaseHandler):
	@gen.coroutine
	def get(self):
		habrachat_cookie = self.get_cookie("habrachat")
		habrachat_user = None
		if habrachat_cookie:
			habrachat_user = yield tornado.gen.Task(self.redis.get, habrachat_cookie)
		if habrachat_user:
			self.write(templates["chat"])
		else:
			self.write(templates["auth"].generate(options = options, request = self.request))
		self.finish()

class LogoutHandler(tornado.web.RequestHandler):
	@gen.coroutine
	def get(self):
		habrachat_cookie = self.get_cookie("habrachat")
		if not habrachat_cookie:
			self.redirect("/")
			return
		
		self.clear_cookie("habrachat")
		self.redirect("/")

class Singleton(type):
	_instances = {}
	def __call__(cls, *args, **kwargs):
		if cls not in cls._instances:
			cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
		return cls._instances[cls]

@six.add_metaclass(Singleton)
class Subscriber(object):
	__metaclass__ = Singleton
	def __init__(self, send_client):
		log.info("Subscribe init")
		self.send_client = send_client
		self.instance_id = _session_id()
		self.sub_client = tornadoredis.Client(host=options.redis_host, port=options.redis_port, password=options.redis_password)
		self.sub_client.connect()
		self.sub_client.subscribe('new_messages', self.subscribe)
		

	def subscribe(self, result):
		log.info("Subscribe new_messages")
		self.sub_client.listen(self.on_message)
		self.send_client.publish("new_messages", json_encode({
			"type":"get_all_users", 
			"instance_id": self.instance_id
		}))

	def on_message(self, message):
		if message.kind == "message":
			chat_message = json_decode(message.body)
			#log.info("%s Get new message by Subscriber from instance:%s"%(self.instance_id, chat_message["instance_id"]))
			if chat_message["instance_id"] == self.instance_id:
				return

			#log.info("Message from %s to %s"%(chat_message["instance_id"], self.instance_id))
			#log.info(message.body)
			if chat_message["type"] == "get_all_users":
				self.send_client.publish("new_messages", json_encode({
					"type":"all_users_sub", 
					"instance_id": self.instance_id,
					"users": list(mp_users.values())
				}))
				return
			elif chat_message["type"] == "all_users_sub":
				for new_user in chat_message["users"]:
					remote_users[new_user["session_id"]] = new_user
					if not (have_remote_users(new_user["id"], new_user["hub"]) or have_local_users(new_user["id"], new_user["hub"])):
						mp_hubs[new_user["hub"]]["users"] += 1
						
						new_user_message = json_encode({
							"type": "new_user",
							"instance_id": self.instance_id,
							"hub": new_user["hub"], #dublicate for simple
							"user": new_user
						})
						for sockets, user in six.iteritems(mp_users):
							if new_user["hub"] == user["hub"]:
								sockets.write_message(new_user_message)

				return
			elif chat_message["type"] == "new_user":
				#log.info("%s New remote user with session_id: %s and id:%s"%(self.instance_id, chat_message["user"]["session_id"], chat_message["user"]["id"]))
				if  not (have_remote_users(chat_message["user"]["id"], chat_message["user"]["hub"]) or have_local_users(chat_message["user"]["id"], chat_message["user"]["hub"])):
					mp_hubs[chat_message["hub"]]["users"] += 1
				remote_users[chat_message["user"]["session_id"]] = chat_message["user"]
			elif chat_message["type"] == "del_user":
				#log.info("%s Start del_user remote_user id:%s session_id:%s"% (self.instance_id, chat_message["user_id"], chat_message["session_id"]))
				try:
					del remote_users[chat_message["session_id"]]
				except KeyError:
					log.error("%s Not found remote_user %s"% (self.instance_id, chat_message["user_id"]))
					#log.error(remote_users)
					return

				
				have_user = have_remote_users(chat_message["user_id"], chat_message["hub"]) or have_local_users(chat_message["user_id"], chat_message["hub"])
				if have_user:
					return
				else:
					mp_hubs[chat_message["hub"]]["users"] -= 1
					
				
			elif chat_message["type"] == "new_message":
				pass
			elif chat_message["type"] == "delete_message":
				pass
			else:
				return
				

			for sockets, user in six.iteritems(mp_users):
				if chat_message["hub"] == user["hub"]:
					try:
						sockets.write_message(message.body)
					except tornado.websocket.WebSocketClosedError:
						pass

@gen.engine
def init_subscribe():
	Subscriber(application.settings["redis"])

def cleints_ping():
	log.info("Ping to %i users."%(len(mp_users)))
	for sockets, user in six.iteritems(mp_users):
		try:
			sockets.ping("ping")
		except tornado.websocket.WebSocketClosedError:
			pass
	tornado.ioloop.IOLoop.instance().add_timeout(datetime.timedelta(seconds=options.ping_every), cleints_ping)

application = tornado.web.Application([
	(r'/start-chat', ChatHandler),
	(r'/auth', AuthHandler),
	(r'/google_auth', GoogleLoginHandler),
	(r'/logout', LogoutHandler),
	(r"/static/(.*)", tornado.web.StaticFileHandler, {"path": options.static_root}),
	(r'/', MainHandler)
])

def set_process_name(name):
	try:
		import setproctitle
		setproctitle.setproctitle(name)
	except:
		pass # Ignore errors, since this is only cosmetic

if __name__ ==  "__main__":
	tornado_config_file = None
	tornado_port = None
	if "HEROKU" in os.environ:
		tornado_config_file = "habrachat_heroku.conf"
		tornado_port = int(os.environ.get("PORT", 5000))
	else:
		tornado_config_file = sys.argv[1]
		tornado_port = int(sys.argv[2])

	tornado.options.parse_config_file(tornado_config_file)

	if "HEROKU" in os.environ:
		try:
			import urlparse
		except:
			import urllib.parse as urlparse
		redis_url = os.environ.get("OPENREDIS_URL") or os.environ.get("REDISTOGO_URL")
		url = urlparse.urlparse(redis_url)
		options.redis_password = url.password
		options.redis_host = url.hostname
		options.redis_port = int(url.port)
		options.google_oauth["key"] = os.environ.get("GOOGLE_OAUTH_KEY")
		options.google_oauth["secret"] = os.environ.get("GOOGLE_OAUTH_SECRET")
	else:
		if len(sys.argv)==4 and sys.argv[3]=="daemon":
			import lockfile, daemon
			log_daemon = open("tornado.log", "a+")
			ctx = daemon.DaemonContext(
				stdout=log_daemon, 
				stderr=log_daemon,
				working_directory=".",

				pidfile=lockfile.FileLock("/tmp/habrachat"+sys.argv[2]+".pid"))
			ctx.open()
	
	server = tornado.httpserver.HTTPServer(application)
	server.bind(tornado_port)
	set_process_name("habrachat")
	
	# start(0) starts a subprocess for each CPU core
	server.start(options.subprocess)
	set_process_name("habrachat")
	

	for hub in options.hubs:
		mp_hubs[hub["name"]] = {
			"name": hub["name"],
			"label": hub["label"],
			"users": 0
		}
		
	current_zone = timezone(options.timezone)

	loader = template.Loader(options.template_root)
	application.settings["loader"] = loader
	templates["auth"] =  loader.load("auth.html")
	templates["chat"] =  loader.load("chat.html").generate(options = options)

	application.settings["google_oauth"]  = options.google_oauth
	application.settings["redis"] = tornadoredis.Client(host=options.redis_host, port=options.redis_port, password=options.redis_password)
	try:
		application.settings["redis"].connect()
	except tornadoredis.ConnectionError:
		log.error("Can't connect to redis.")
	else:
		# Delayed initialization of settings
		tornado.ioloop.IOLoop.instance().add_callback(init_subscribe)
		#if options.ping_every:
		#	tornado.ioloop.IOLoop.instance().add_timeout(datetime.timedelta(seconds=options.ping_every), cleints_ping)
		tornado.ioloop.IOLoop.instance().start()