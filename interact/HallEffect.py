from MomentarySwitch import MomentarySwitch

class HallEffect(MomentarySwitch):
	def __init__(self, pig, pin, callback=None, release_callback=None):
		super(HallEffect, self).__init__(pig, pin, False, bouncetime=0, \
			callback=callback, release_callback=release_callback)

		self.listen()