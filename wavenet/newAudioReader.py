import os
import re
import midi
import random
import librosa
import fnmatch
import threading
import numpy as np
import tensorflow as tf

def find_files(dir, format):
	'''Recursively finds all files matching the pattern.'''
	files = []
	for root, dirnames, filenames in os.walk(directory):
		for filename in fnmatch.filter(filenames, pattern):
			files.append(os.path.join(root, filename))
	return files

def load_files(data_dir, sample_rate, gc_enabled, lc_enabled, lc_fileformat):
	# get all audio files and print their number
	audio_files = find_files(data_dir, '*.wav')
	print("Number of audio files is {}".format(len(audio_files)))

	if lc_enabled:
		lc_files = find_files(data_dir, lc_fileformat)
		print("Number of midi files is {}".format(len(lc_files)))

		# Now make sure the files correspond and are in the same order
		audio_files, lc_files = clean_midi_files(audio_files, lc_files)
		print("File clean up done. Final file count is {}".format(len(audio_files)))

	randomized_files = randomize_files(audio_files)
	for filename in randomized_files:
		# get GC embedding here if using it

		# now load audio file using librosa, audio is now a horizontal array of float32s
		# throwaway _ is the sample rate returned back
		audio, _ = librosa.load(filename, sr = sample_rate, mono = True)
		
		# this reshape makes it a vertical array
		audio = audio.reshape(-1, 1)

		# TODO: This is where we get the GC ID mapping from audio
		# later, we can add support for conditioning on genre title, etc.
		# gc_id = get_gc_id(filename)

		# now we get the LC timeseries file here
		# load in the midi or any other local conditioning file
		if lc_enabled:
			midi_name = os.path.splitext(filename)[0] + ".mid"
			# returns list of events with ticks in relative time
			lc_timeseries = midi.read_midifile(midi_name)

		yield audio, filename, gc_id, lc_timeseries

def clean_midi_files(audio_files, lc_files):
	# mapping both lists of files to lists of strings to compare them
	# note: in Python 3 map() returns a map object, which can still be iterated through (list() not needed)
	str_audio = map(str, audio_files)
	str_midi = map(str, lc_files)

	# remove extensions
	for wav in enumerate(str_audio):
		str_audio(wav) = os.path.splitext(str_audio(wav))[0]

	for midi in enumerate(str_midi):
		str_midi(midi) = os.path.splitext(str_midi(midi))[0]

	# create two lists of the midi and wav mismatches
	str_midi = [midi for midi in str_midi if midi not in str_audio]
	str_audio = [wav for wav in str_audio if wav not in str_midi]

	for wav in str_audio:
		fname = wav + ".wav"
		audio_files.remove(fname)
		print("No MIDI match found for .wav file {}. Raw audio file removed.".format(fname))

	for midi in str_midi:
		fname = midi + ".mid"
		lc_files.remove(fname)
		print("No raw audio match found for .mid file {}. MIDI file removed.".format(fname))
		
	return audio_files, lc_files

	
def map_midi(audio, lc_timeseries)
	'''Upsampling midi and mapping it to the wav samples.'''
	# TODO
	

def trim_silence(audio, threshold, frame_length = 2048):
	'''Removes silence at the beginning and end of a sample.'''
	if audio.size < frame_length:
		frame_length = audio.size
	energy = librosa.feature.rmse(audio, frame_length=frame_length)
	frames = np.nonzero(energy > threshold)
	indices = librosa.core.frames_to_samples(frames)[1]

	# Note: indices can be an empty array, if the whole audio was silence.
	return audio[indices[0]:indices[-1]] if indices.size else audio[0:0]

class AudioReader():
	def __init__(self,
				 data_dir,
				 coord,
				 sample_rate = 16000,
				 gc_enabled = False,
				 lc_enabled = False,
				 lc_fileformat = None,
				 receptive_field,
				 sample_size = None,
				 silence_threshold = None,
				 q_size = 32):
					 
		# Input member vars initialiations
		self.data_dir = data_dir
		self.coord = coord
		self.sample_rate = sample_rate
		self.gc_enabled = gc_enabled
		self.lc_enabled = lc_enabled
		self.lc_fileformat = lc_fileformat
		self.receptive_field = receptive_field
		self.sample_size = sample_size
		self.silence_threshold = silence_threshold
		self.q_size = q_size

		# Non-input member vars initialization
		self.threads = []
		
		# DATA QUEUES

		# Audio samples are float32s with encoded as a one hot, so shape is 1 X quantization_channels
		self.audio_placeholder = tf.placeholder(dtype = tf.float32, shape = None)
		self.q_audio = tf.PaddingFIFOQueue(capacity = q_size, dtypes = [tf.float32], shapes = [(None, 1)])
		self.enq_audio = self.q_audio.enqueue([self.audio_placeholder])


		if self.gc_enabled:
			# GC samples are embedding vectors with the shape of 1 X GC_channels
			self.gc_placeholder = tf.placeholder(dtype = tf.int32, shape = ())
			self.q_gc = tf.PaddingFIFOQueue(capacity = q_size, dtypes = [tf.int32], shapes = [(None, 1)])
			self.enq_gc = self.q_gc.enqueue([self.gc_placeholder])

		if self.lc_enabled:	
			# LC samples are embedding vectors with the shape of 1 X LC_channels
			self.lc_placeholder = tf.placeholder(dtype = tf.int32, shape = None)
			self.q_lc = tf.PaddingFIFOQueue(capacity = q_size, dtypes = [tf.int32], shapes = [(None, 1)])
			self.enq_lc = self.q_lc.enqueue([self.lc_placeholder])

		# now load in the files and see if they exist
		audio_files = find_files(self.data_dir, '*.wav')
		if not audio_files:
			raise ValueError("No WAV files found in '{}'.".format(data_dir))
		
		# if LC is enabled, check if local conditioning files exist
		if lc_enabled:
			lc_files = find_files(self.data_dir, self.lc_fileformat)
			if not lc_files:
				raise ValueError("No MIDI files found in '{}'".format(self.data_dir))

		def dq_audio(self, num_elements):
			return self.q_audio.dequeue_many(num_elements)

		def dq_gc(self, num_elements):
			return self.q_gc.dequeue_many(num_elements)

		def dq_lc(self, num_elements):
			return self.q_lc.dequeue_many(num_elements)

		def input_stream(self, sess):
			stop = False

			# keep looping until training is done
			while not stop:
				iterator = load_files(self.data_dir, self.sample_rate, self.gc_enabled, self.lc_enabled, self.lc_fileformat)

			for audio, filename, gc_id, lc_timeseries in iterator:
				if self.coord.should_stop():
					stop = True
					break

				# TODO: If we remove this silence trimming we can use the randomised queue
				# instead of the padding queue so that we dont have to take care of midi with silence
				if self.silence_threshold is not None:
					audio = trim_silence(audio[:, 0], self.silence_threshold)
					audio = audio.reshape(-1, 1)

					# now check if the whole audio was trimmed away
					if audio.size = 0:
						print("Warning: {} was ignored as it contains only "
							  "silence. Consider decreasing trim_silence "
							  "threshold, or adjust volume of the audio."
							  .format(filename))

					# now pad beginning of samples with n = receptive_field number of 0s 
					# TODO: figure out why we are padding this ???
					audio = np.pad(audio, [[self.receptive_field, 0], [0, 0]], 'constant')

					# now 
					if self.sample_size:
						# TODO: understand the reason for this piece voodoo from the original reader
						while len(audio) > self.receptive_field:
							piece = audio[:(self.receptice_field + self.sample_size), :]
							sess.run(self.enq_audio, feed_dict = {self.audio_placeholder : piece})

							# add GC mapping to q if enabled
							if self.gc_enabled:
								sess.run(self.enq_gc, feed_dict = {self.gc_placeholder : gc_id})

							# add LC mapping to queue if enabled
							if self.lc_enabled:
								# TODO:
								# lc = map_midi(piece)
								sess.run(self.enq_lc, feed_dict = {self.lc_placeholder : lc_encode})
					else:
						# otherwise feed the whole audio sample in its entireity
						sess.run(self.enq_audio, feed_dict = {self.audio_placeholder : audio})

						# add GC mapping to q if enabled
						if gc_enabled:
							sess.run(self.enq_gc, feed_dict = {self.gc_placeholder : gc_id})
						
						# add LC mapping to queue if enabled
						if lc_enabled:
							# TODO: this is where the midi gets upsampled and mapped to the wav samples
							# lc = map_midi(audio, start_sample, lc_timeseries)
							sess.run(self.enq_lc, feed_dict = {self.lc_placeholder : lc_encode})



		def start_threads(self, sess, n_threads = 1):
			for _ in range(n_threads):
				thread = threading.Thread(target = self.input_stream, args = (sess,))
				thread.daemon = True  # Thread will close when parent quits.
				thread.start()
				self.threads.append(thread)
			return self.threads



# Template for the midi mapper
class MidiMapper():
	
	def __init__(self,
				start_sample,
				sample_rate,
				end_sample,
				midi,
				q_size):
		
		self.start_sample = start_sample
		self.sample_rate = sample_rate
		self.end_sample = end_sample
		self.midi = midi
		self.q_size = q_size
		self.tempo, self.ticks_per_beat = get_midi_metadata(self.midi)
		self.lc_q = tf.FIFOQueue(capacity = self.q_size, dtypes = [tf.uint8,], name = "lc_embeddings_q" )
		

	def sample_to_milliseconds(self, sample_num, sample_rate):
		'''takes in a sample number of the wav and the sample rate and 
			gets the corresponding millisecond of the sample in the song'''
		return 1000 * sample_num / sample_rate
		
		
	def tick_delta_to_milliseconds(self, delta_ticks):
		'''converts a range of midi ticks into a range of milliseconds'''
		return self.tempo * delta_ticks / self.ticks_per_beat
		
	def gap_threshold(self):
		# returns longest time gap accepted between wav and midi file - half a beat
		return tick_delta_to_milliseconds(self.ticks_per_beat) / 2
	
	def get_midi_metadata(self):
		'''gets all the metadata here from the midi file header'''
		
		return tempo, ticks_per_beat
		
		
	def enq_embeddings(self, upsample_time, note_state):
		'''takes in the notes to be upsampled as a state array and the time to be upsampled for 
		and then upsamples the notes according to the wav sampling rate, makes embeddings and adds them  
		to the tf queue''' 
		
		
		
	def upsample(self, midi, sample_rate, start_sample = 0, end_sample = None):
		
		# stores the current state of the midi: ie. which notes are on 
		note_state = []

		# input midi is the midi pattern, the output of read_midifile. Assume its format and get the first track of the midi
		# This track is a list of events occurring in the midi
		midi_track = midi[0]
		
		# First get the start and end times of the midi section to be extracted and upsampled
		current_time = sample_to_milliseconds(start_sample, sample_rate)
		end_time = sample_to_milliseconds(end_sample, sample_rate)

		# now to avoid making a vector every single loop iteration, make a zeros embedding vector here
		# use tf.uint8 to save memory since we will most likely not need more than 256 embeddings	
		empty_embedding = tf.zeros(shape = [1, lc_channels], dtype = tf.unit8)
		
		counter = 0 #placeholder - will be first NoteOn
		while current_time is not end_time:
			# first get the current midi event
			curr_event = midi_track[counter]
			
			# extract the time tick deltas and the event types form the midi
			delta_ticks = curr_event.tick
			event_name  = curr_event.name
			event_data  = curr_event.data
			
			if   event_name is "Note On"  and delta_ticks is 0:
				# add note (data[0])
				note_state.append(event_data[0])
				
			elif event_name is "Note On"  and delta_ticks is not 0:
				# first add to embeddings and then add note to state array, then update time
				
				# convert tick range into time range
				upsample_time = tick_delta_to_milliseconds(delta_ticks)
				
				# upsample urrent state of notes into the embeddings and add to lc_queue
				enq_embeddings(upsample_time, note_state)
				
				# and then add the note to the state
				note_state.append(event_data[0])
				
			elif event_name is "Note Off" and delta_ticks is 0:
				# take out of state array by value
				note_state.remove(event_data[0])
				
			elif event_name is "Note Off" and delta_ticks is not 0:
				# first add to embeddings and then take out of state array, then update time
				upsample_time = tick_delta_to_milliseconds(delta_ticks)
				
				# upsample urrent state of notes into the embeddings and add to lc_queue
				enq_embeddings(upsample_time, note_state)
				
				# and then remove the note from the state
				note_state.remove(event_data[0])
				
			elif event_name is "End of Track":
				# warn if gap between midi and wav, then update time
				# the embedding is already zero-padded, so no need to pad it
				# get the bpm and find how many seconds for one beat and then half that
				if (end_time - current_time) > gap_threshold():
					# the MIDI ended, but the .wav sample hasn't reached its end
					print("The given .wav file is longer than the matching MIDI file. Please check that the MIDI and .wav line up correctly.")
					current_time = end_time # to break outer while loop
				else:
					current_time = end_time # if not already, to break outer while loop
			
			elif event_name is "Set Tempo" and delta_ticks is 0:
				
				# mid-song tempo change
				self.tempo = new_tempo
			elif event_name is "Set Tempo" and delta_ticks is not 0:
				# 
				upsampe_time = ticks_to_milliseconds(delta_ticks)
				enq_embedddings(upsample_time, note_state)
			else:
				# We are ignoring events other than note on/off or tempo
				print("Event other than Note On/Off, Tempo Change, or End of Track detected. Event ignored. Continuing.")
				continue

			# inc
			counter += 1
			current_time = current_time + tick_delta_to_milliseconds(delta_ticks)
			
		# current_time = end_time, but the MIDI isn't at the end of the track yet
		if midi_track[counter].name is not "End of Track":
			print("The given MIDI file is longer than the matching .wav file. Please check that the MIDI and .wav line up correctly.")
			# then continue like it isn't our fault
			
		
		
		
		
