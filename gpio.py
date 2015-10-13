import os, requests, logging
from threading import Thread
from time import sleep

from utils import start_daemon, stop_daemon, get_config, str_to_bool
from vars import PROD_MODE, BASE_DIR

mock_gpio = False

try:
	mock_gpio = get_config('mock_gpio')
except KeyError as e:
	pass	

class MPGPIO():
	"""GPIO utilities for our RPi.
	"""

	def __init__(self):
		logging.basicConfig(filename=self.conf['d_files']['gpio']['log'], level=logging.DEBUG)

	def start_gpio(self):
		if mock_gpio:
			logging.info("starting mocked gpio.")
			self.db.set('GPIO_STATUS', True)
			return 

		start_daemon(self.conf['d_files']['gpio'])

		for mapping in self.gpio_mappings:
			mapping.start()

		logging.info("GPIO listening...")
		self.db.set('GPIO_STATUS', True)

	def stop_gpio(self):
		if mock_gpio:
			logging.info("stopping mocked gpio.")
			self.db.set('GPIO_STATUS', False)
			return 

		stop_daemon(self.conf['d_files']['gpio'])
		self.db.set('GPIO_STATUS', False)
		
		logging.info("GPIO Stopped")

	def get_gpio_status(self):
		# maybe this will be something more substantial...
		return str_to_bool(self.db.get('GPIO_STATUS'))

class GPIOThread(Thread):
	def __init__(self):
		Thread.__init__(self)

	def run(self):
		logging.debug("GPIO THREAD STARTED...")

		while True:
			self.parse_state()
			sleep(0.005)

	def terminate(self):
		logging.debug("terminating GPIO Thread...")

	def parse_state(self):
		logging.debug("parsing GPIO state...")
		pass

	def send(self, endpoint):
		url = "http://localhost:%d/%s" % (self.conf['api_port'], endpoint)
		
		try:
			r = requests.get(url)
			logging.info(r.content)
		except Exception as e:
			logging.warning("Could not perform request to %s: " % url)
			
			if PROD_MODE == "debug":
				print e, type(e)

class ReceiverThread(GPIOThread):
	def __init__(self):
		GPIOThread.__init__(self)

	def on_hang_up(self):
		super(ReceiverThread, self).send("hang_up")

	def on_pick_up(self):
		super(ReceiverThread, self).send("pick_up")

class IRReceiverThread(ReceiverThread):
	def __init__(self):
		ReceiverThread.__init__(self)
		from interact.VCNL4010 import VCNL4010

		self.gpio = VCNL4010()
		self.gpio.continuous_conversion_on()

	def parse_state(self):
		super(ReceiverThread, self).parse_state()
		
		logging.debug("proximity is %d" % self.gpio.read_proximity())
		logging.debug("ambient light is %d" % self.gpio.read_ambient())

		# and decide whether to pickup or hang up based on this...
		# might need to set values in config to declare threshold per sculpture

class HallEffectReceiverThread(ReceiverThread):
	def __init__(self, pin):
		ReceiverThread.__init__(self)
		from interact.MomentarySwitch import MomentarySwitch

		self.gpio = MomentarySwitch(pin, False, \
			callback=self.on_pick_up, release_callback=self.on_hang_up, bouncetime=0)

	def on_pick_up(self, gpio, level, tick):
		logging.debug("Hall Effect Pickup: (level: %s, tick: %s)" % (str(level), str(tick)))
		super(ReceiverThread, self).on_pick_up()

	def on_hang_up(self, gpio, level, tick):
		logging.debug("Hall Effect Hangup: (level: %s, tick: %s)" % (str(level), str(tick)))
		super(ReceiverThread, self).on_hang_up()

	def terminate(self):
		self.gpio.pig.stop()
		super(HallEffectReceiverThread, self).terminate()

class ButtonThread(GPIOThread):
	def __init__(self, pin):
		GPIOThread.__init__(self)
		from interact.MomentarySwitch import MomentarySwitch

		self.gpio = MomentarySwitch(pin, True, self.on_button_press)

	def on_button_press(self, gpio, level, tick):
		logging.debug("Simple button pressed! (level: %s, tick: %s)" % (str(level), str(tick)))
		super(ButtonThread, self).send("mapping/%d" % self.gpio.pin)

	def terminate(self):
		self.gpio.pig.stop()
		super(ButtonThread, self).terminate()


class MatrixKeypadThread(GPIOThread):
	def __init__(self, columm_pins, row_pins):
		GPIOThread.__init__(self)
		from interact.MatrixKeypad import MatrixKeypad

		self.gpio = MatrixKeypad(columm_pins, row_pins)
		
	def parse_state(self):
		super(MatrixKeypadThread, self).parse_state()

		key_pressed = self.gpio.getKey()
		if key_pressed is None:
			return 

		logging.debug("MatrixKeypad key pressed: %d" % key_pressed)
		self.on_key_press(key_pressed)

	def on_key_press(self, key):
		super(MatrixKeypadThread, self).send("mapping/%d" % key)

	def terminate(self):
		self.gpio.pig.stop()
		super(MatrixKeypadThread, self).terminate()


