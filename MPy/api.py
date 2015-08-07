import signal, os, redis, json, logging
from multiprocessing import Process
import tornado.ioloop, tornado.httpserver, tornado.web

from ivr import MPIVR
from rpi import MPRPi

from utils import start_daemon, stop_daemon, get_config
from vars import PROD_MODE, BASE_DIR, GATHER_MODE, RESPOND_MODE

def terminationHandler(signal, frame): exit(0)
signal.signal(signal.SIGINT, terminationHandler)

class MPServerAPI(tornado.web.Application, MPIVR, MPRPi):
	"""The REST API.

	To be used to direct communication flows between the circuitry inputs and outputs.

	"""
	
	def __init__(self, gpio_mappings=None):
		print("init MPSAPI")

		api_port, num_processes, redis_port, rpi_id, processing_port = \
			get_config(['api_port', 'num_processes', 'redis_port', 'rpi_id', 'processing_port'])

		self.conf = {
			'rpi_id' : rpi_id,
			'd_files' : {
				'api' : {
					'pid' : os.path.join(BASE_DIR, ".monitor", "server.pid.txt"),
					'log' : os.path.join(BASE_DIR, ".monitor", "%s.log.txt" % rpi_id),
					'ports' : api_port
				},
				'gpio' : {
					'log' : os.path.join(BASE_DIR, ".monitor", "%s.log.txt" % rpi_id),
					'pid' : os.path.join(BASE_DIR, ".monitor", "gpio.pid.txt")
				},
				'ivr' : {
					'log' : os.path.join(BASE_DIR, ".monitor", "%s.log.txt" % rpi_id)
				},
				'module' : {
					'log' : os.path.join(BASE_DIR, ".monitor", "%s.log.txt" % rpi_id),
					'pid' : os.path.join(BASE_DIR, ".monitor", "%s.pid.txt" % rpi_id)
				}
			},
			'api_port' : api_port,
			'num_processes' : num_processes,
			'redis_port' : redis_port,
			'processing_port' : processing_port,
			'media_dir' : os.path.join(BASE_DIR, "core", "media")
		}

		self.routes = [
			("/", self.TestHandler),
			(r'/js/(.*)', tornado.web.StaticFileHandler, \
				{ 'path' : os.path.join(BASE_DIR, "core", "MPy", "test_pad", "js")}),
			("/status", self.StatusHandler),
			("/pick_up", self.PickUpHandler),
			("/hang_up", self.HangUpHandler),
			(r'/mapping/(\d+)', self.MappingHandler)
		]

		self.gpio_mappings = gpio_mappings

		logging.basicConfig(filename=self.conf['d_files']['api']['log'], level=logging.DEBUG)

	def start(self):
		logging.info("Start invoked.")

		MPIVR.__init__(self)
		MPRPi.__init__(self)

		# start processing sketch

		p = Process(target=self.start_api)
		p.start()

		p = Process(target=self.start_RPi)
		p.start()

		

		return True

	def stop(self):
		logging.info("Stop invoked.")

		self.stop_RPi()
		self.stop_api()
		# stop processing sketch
		
		return True

	class TestHandler(tornado.web.RequestHandler):
		def get(self):
			self.render("test_pad/index.html")

	class StatusHandler(tornado.web.RequestHandler):
		def get(self):
			logging.info("getting status")
			result = self.application.get_status()

			self.set_status(200 if result['ok'] else 400)
			self.finish(result)

	class PickUpHandler(tornado.web.RequestHandler):
		def get(self):
			logging.info("pick up: rpi_id %s" % self.application.conf['rpi_id'])
			
			result = self.application.on_pick_up()

			self.set_status(200 if result['ok'] else 400)
			self.finish(result)

	class HangUpHandler(tornado.web.RequestHandler):
		def get(self):
			logging.info("hang up: rpi_id %s" % self.application.conf['rpi_id'])
			
			result = self.application.on_hang_up()

			self.set_status(200 if result['ok'] else 400)
			self.finish(result)

	class MappingHandler(tornado.web.RequestHandler):
		def get(self, pin):
			pin = int(pin)

			logging.info("pin %d: rpi_id %s" % (pin, self.application.conf['rpi_id']))
			if self.application.db.get('IS_HUNG_UP'):
				self.set_status(400)
				self.finish({ 'ok' : False, 'error' : "Phone is hung up"})
				return

			# play dmtf tone;
			command = { "press" : self.application.map_pin_to_tone(pin) }
			
			mode = int(self.application.db.get('MODE'))
			logging.info("CURRENT MODE: %d" % mode)

			if mode == GATHER_MODE:
				logging.info("THIS IS A GATHER: %d" % pin)

				try:
					gathered_keys = json.loads(self.application.db.get('gathered_keys'))
					print gathered_keys
				except Exception as e:
					logging.warning("could not get gathered_keys: ")
					
					if PROD_MODE == "debug":
						print e, type(e)

					gathered_keys = []

				gathered_keys += [pin]
				self.application.db.set('gathered_keys', json.dumps(gathered_keys))
			else:
				self.application.on_key_pressed(pin)
			
			result = self.application.send_command(command)

			self.set_status(200 if result['ok'] else 400)
			self.finish(result)

	def map_pin_to_tone(self, pin):
		# shifted by default!

		if pin not in [12, 13, 14]:
			tone = str(pin - 2)
		elif pin == 12:
			tone = 's'
		elif pin == 13:
			tone = str(0)
		elif pin == 14:
			tone = 'p'
		 
		return tone

	def on_key_pressed(self, key):
		logging.info("on key pressed: %d" % key)

	def reset_for_call(self):
		self.db.set('IS_HUNG_UP', False)
		self.db.set('MODE', RESPOND_MODE)
		self.db.set('gathered_keys', None)

	def on_pick_up(self):
		logging.info("picking up")

		self.reset_for_call()

		p = Process(target=self.run_script)
		p.start()

		return { 'ok' : not self.db.get('IS_HUNG_UP') }

	def on_hang_up(self):
		logging.info("hanging up")

		stop_daemon(self.conf['d_files']['module'])
		self.db.set('IS_HUNG_UP', True)

		return { 'ok' : self.db.get('IS_HUNG_UP') }

	def get_status(self):
		logging.info("getting status")
		
		status = { 'ok' : True }
		
		# can we ping processing?
		# is gpio ok?

		return status

	def run_script(self):
		start_daemon(self.conf['d_files']['module'])

	def start_api(self):
		"""Starts API, initializes the redis database, and daemonizes all processes so they may be restarted or stopped.
		"""

		logging.info("starting MPSAPI")

		self.db = redis.StrictRedis(host='localhost', port=self.conf['redis_port'], db=0)
		self.db.set('MODE', RESPOND_MODE)

		tornado.web.Application.__init__(self, self.routes)
		server = tornado.httpserver.HTTPServer(self)

		try:
			server.bind(self.conf['api_port'])
		except Exception as e:
			from fabric.api import settings, local
			from fabric.context_managers import hide

			with settings(hide('everything'), warn_only=True):
				local("kill $(lsof -t -i:%d)" % self.conf['api_port'])

			server.bind(self.conf['api_port'])

		start_daemon(self.conf['d_files']['api'])
		server.start(self.conf['num_processes'])
		tornado.ioloop.IOLoop.instance().start()

	def stop_api(self):
		"""Stops the API.
		"""

		# don't forget to save the redis db
		stop_daemon(self.conf['d_files']['api'])
