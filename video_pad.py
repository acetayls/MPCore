import os, re, json, logging
from fabric.api import settings, local
from fabric.context_managers import hide
from subprocess import Popen, PIPE, STDOUT
from multiprocessing import Process, Queue
from time import sleep, time

from utils import start_daemon, stop_daemon, time_str_to_millis, millis_to_time_str
from vars import BASE_DIR

class MPVideoPad(object):
	OMX_CMD = {
		'setup' : "omxplayer -I --no-osd -o local %s < %s",
		'exe' : "echo -n %s > %s"
	}

	class VideoMappingTemplate():
		def __init__(self, video, log_path, index=0):
			self.src = video
			self.index = index
			self.fifo = os.path.join(BASE_DIR, ".monitor", "omxplayer_%d.fifo" % self.index)
			self.d_files = {
				'log' : log_path,
				'pid' : os.path.join(BASE_DIR, ".monitor", "omxplayer_%d.pid.txt" % self.index)
			}

	def __init__(self):
		logging.basicConfig(filename=self.conf['d_files']['vid']['log'])
		self.video_mappings = []

		for root, _, files in os.walk(os.path.join(BASE_DIR, "media", "video", "viz")):
			for v, video in enumerate([v for v in files if re.match(r'.*\.mp4$', v)]):
				video_mapping = self.VideoMappingTemplate(os.path.join(root, video), \
					self.conf['d_files']['vid']['log'], v)
				
				self.video_mappings.append(video_mapping)
			
			break

	def get_video_mapping_by_filename(self, video):
		try:
			return [vm for vm in self.video_mappings if \
				re.match(re.compile(".*/%s$" % video), vm.src)][0]
		except Exception as e:
			logging.error("No video found for %s" % video)
		
		return None

	def get_video_info(self, index):
		try:
			return json.loads(self.db.get("video_%d" % index))
		except Exception as e:
			logging.error("NO INFO FOR VIDEO %d" % index)

		return None

	def start_video_pad(self):
		return True

	def stop_video_pad(self):
		with settings(hide('everything'), warn_only=True):
			for omx_pid in self.get_omx_instances():
				local("kill -9 %d" % omx_pid)

			for video_mapping in self.video_mappings:
				local("rm %s" % video_mapping.fifo)

		return True

	def get_omx_instances(self, video=None):
		omx_pids = []
		
		with settings(hide('everything'), warn_only=True):
			omx_instances = local("ps ax | grep -v grep | grep omxplayer", capture=True)
			
			if not omx_instances.succeeded:
				return omx_pids

			for line in omx_instances.split('\n'):
				if video is not None and not re.match(video, line):
					print "SKIPPING THIS BECAUSE IT DOES NOT CONCERN VIDEO %s" % line
					continue
				
				try:
					omx_pids.append(int(re.findall(r'(\d+)\s.*', line)[0]))
				except Exception as e:
					print "OOPS"
					print e, type(e)
					continue

		return omx_pids

	def stop_video(self, video=None, video_callback=None):
		if video is None:
			video_mapping = self.video_mappings[0]
		else:
			video_mapping = self.get_video_mapping_by_filename(video)
			if video_mapping is None:
				logging.err("NO VIDEEO %s TO STOP!" % video)
				return False

		with settings(warn_only=True):
			for omx_pid in self.get_omx_instances(video=video):
				local("kill -9 %s" % int(omx_pid))

			local("rm %s" % video_mapping.fifo)

		logging.debug("stopping video #%d (%s)" % (video_mapping.index, video_mapping.src))
		return True

	def play_video(self, video, with_extras=None, video_callback=None):
		video_mapping = self.get_video_mapping_by_filename(video)

		# make fifo
		with settings(warn_only=True):
			if os.path.exists(video_mapping.fifo):
				local("rm %s" % video_mapping.fifo)

			local("mkfifo %s" % video_mapping.fifo)

		p = Process(target=self.setup_video, args=(video_mapping, with_extras, video_callback))
		p.start()
		
		# set playing
		with settings(warn_only=True):
			local(self.OMX_CMD['exe'] % ('p', video_mapping.fifo))
			start_time = time()
			local(self.OMX_CMD['exe'] % ('p', video_mapping.fifo))

		if video_callback is not None:
			video_callback({'index' : video_mapping.index, 'info' : {'start_time' : start_time}})

		return True

	def setup_video(self, video_mapping, with_extras=None, video_callback=None):
		logging.debug("setting up video #%d (%s)" % (video_mapping.index, video_mapping.src))
		
		# load video into omxplayer on fifo. this will block.
		start_daemon(video_mapping.d_files)
		
		setup_cmd = self.OMX_CMD['setup']
		if with_extras is not None:
			setup_cmd = setup_cmd.replace("-I", "-I %s " % " ".join(\
				["--%s %s" % (e, with_extras[e]) for e in with_extras.keys()]))

		logging.debug("setup command: %s" % setup_cmd)

		p = Popen(setup_cmd % (video_mapping.src, video_mapping.fifo), \
			shell=True, stdout=PIPE, stderr=STDOUT)

		while True:
			duration_line = re.findall(r'Duration\:\s+(.*),.*', p.stdout.readline())
			if len(duration_line) == 1:
				duration_str = duration_line[0].split(",")[0]
				duration = {
					'millis' : time_str_to_millis(duration_str),
					'str' : duration_str
				}

				if video_callback is not None:
					video_callback({'index' : video_mapping.index, \
						'info' : {'duration' : duration}})

				break

		stop_daemon(video_mapping.d_files)		
	
	def pause_video(self, video=None, unpause=False, video_callback=None):
		if video is None:
			video_mapping = self.video_mappings[0]
		else:
			video_mapping = self.get_video_mapping_by_filename(video)
			if video_mapping is None:
				logging.err("NO VIDEEO %s TO PLAY/PAUSE!" % video)
				return False

		logging.debug("play/pausing video #%d (%s)" % (video_mapping.index, video_mapping.src))

		with settings(warn_only=True):
			pause_time = time()
			local(self.OMX_CMD['exe'] % ('p', video_mapping.fifo))

			if video_callback is not None:
				info = {}
				
				if not unpause:
					old_info = self.get_video_info(video_mapping.index)
					position = 0 if 'position_at_last_pause' not in old_info.keys() else old_info['position_at_last_pause']
					
					info['last_pause_time'] = pause_time
					info['position_at_last_pause'] = (position + abs(pause_time - old_info['start_time']))

				else:
					info['start_time'] = pause_time

				video_callback({'index' : video_mapping.index, 'info' : info})

		return True

	def unpause_video(self, video=None, video_callback=None):
		logging.debug("unpausing video")

		return self.pause_video(video=video, unpause=True, video_callback=video_callback)

	def move_video(self, video, placement, with_extras=None, video_callback=None):
		video_mapping = self.get_video_mapping_by_filename(video)
		if video_mapping is None:
			logging.err("NO VIDEEO %s TO MOVE!" % video)
			return False

		logging.debug("moving video #%d (%s)" % (video_mapping.index, video_mapping.src))

		# pause
		if not self.pause_video(video=video, video_callback=video_callback):
			return False

		# stop
		if not self.stop_video(video=video, video_callback=video_callback):
			return False

		# setup with extras
		if with_extras is None:
			with_extras = {}

		if placement is not None:
			with_extras['win'] = placement
		
		try:
			with_extras['pos'] = millis_to_time_str(self.get_video_info(video_mapping.index)['position_at_last_pause'] * 1000)
		except Exception as e:
			print e, type(e)

		if self.play_video(video=video, with_extras=with_extras, video_callback=video_callback):
			if video_callback is not None:
				video_callback({'index' : video_mapping.index, 'current_placement' : placement})
			
			return True

		return False

