import os
import re
import midi
import random
import librosa
import fnmatch
import threading
import numpy as np
import tensorflow as tf
import time
import queue

# TODO: make sure that set tempo evnets cannot have a tick delta associated with them

def find_files(directory, pattern):
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
		print("File clean up done. Final file count is {}".format(len(audio_files) + len(lc_files)))

	# Returns a generator
	randomized_files = randomize_files(audio_files)

	for filename in randomized_files:
		# get GC embedding here if using it

		# now load audio file using librosa, audio is now a horizontal array of float32s
		# throwaway _ is the sample rate returned back
		audio, _ = librosa.load(filename, sr = sample_rate, mono = True)
		
		# this reshape makes it a vertical array
		audio = audio.reshape(-1, 1)

		# ADAPT: This is where we get the GC ID mapping from audio
		# later, we can add support for conditioning on genre title, etc.
		if gc_enabled:
			gc_id = None
			# gc_id = get_gc_id(filename)
		else:
			gc_id = None # get_gc_id(filename)


		# now we get the LC timeseries file here
		# load in the midi or any other local conditioning file
		if lc_enabled:
			# entire path name
			midi_name = os.path.splitext(filename)[0] + ".mid"
			# This is the entire midi pattern, including the track
			lc_timeseries = midi.read_midifile(midi_name)
		else:
			lc_timeseries = None

		# returns generator
		yield audio, filename, gc_id, lc_timeseries


def clean_midi_files(audio_files, lc_files):
	# mapping both lists of files to lists of strings to compare them
	str_audio = np.char.mod('%s', audio_files)
	str_midi = np.char.mod('%s', lc_files)

	# remove extensions
	for i, wav_name in enumerate(str_audio):
		str_audio[i] = os.path.splitext(str_audio[i])[0] 

	for i, midi_name in enumerate(str_midi):
		str_midi[i] = os.path.splitext(str_midi[i])[0]

	# create two lists of the midi_name and wav_name mismatches
	str_midi_missing = [wav_name for wav_name in str_audio if wav_name not in str_midi]
	str_wav_missing = [midi_name for midi_name in str_midi if midi_name not in str_audio]

	for wav_name in str_midi_missing:
		fname = wav_name + ".wav"
		audio_files.remove(fname)
		print("No MIDI match found for .wav file {}. Raw audio file removed.".format(fname))

	for midi_name in str_wav_missing:
		fname = midi_name + ".mid"
		lc_files.remove(fname)
		print("No raw audio match found for .mid file {}. MIDI file removed.".format(fname))
		
	return audio_files, lc_files
	

def randomize_files(files):
	for file in files:
		file_index = random.randint(0, (len(files) - 1))
		#print("called")
		yield files[file_index]


def trim_silence(audio, threshold, frame_length = 2048):
	'''Removes silence at the beginning and end of a sample.'''
	if audio.size < frame_length:
		frame_length = audio.size
	energy = librosa.feature.rmse(audio, frame_length=frame_length)
	frames = np.nonzero(energy > threshold)
	indices = librosa.core.frames_to_samples(frames)[1]

	# Note: indices can be an empty array, if the whole audio was silence.
	return audio[indices[0]:indices[-1]] if indices.size else audio[0:0]

class LCAudioReader():
	def __init__(self,
				data_dir,
				coord,
				receptive_field,
				gc_enabled = False,
				lc_enabled = False,
				lc_channels = None,
				lc_fileformat = None,
				sample_rate = 16000,
				sample_size = None,
				silence_threshold = None,
				q_size = 32,
				sess = None):
		# Input member vars initialiations
		self.data_dir = data_dir
		self.coord = coord
		self.sample_rate = sample_rate
		self.gc_enabled = gc_enabled
		self.lc_enabled = lc_enabled
		self.lc_channels = lc_channels
		self.lc_fileformat = lc_fileformat
		self.receptive_field = receptive_field
		self.sample_size = sample_size
		self.silence_threshold = silence_threshold
		self.q_size = q_size
		self.sess = sess

		# Non-input member vars initialization
		self.threads = []
		
		# DATA QUEUES

		# Audio samples are float32s that have not yet been encoded into one hots
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
			self.lc_placeholder = tf.placeholder(dtype = tf.float32, shape = (None, self.lc_channels))
			self.q_lc = tf.PaddingFIFOQueue(capacity = q_size, dtypes = [tf.float32], shapes = [(None, self.lc_channels)])
			self.enq_lc = self.q_lc.enqueue([self.lc_placeholder])

		# now load in the files and see if they exist
		audio_files = find_files(self.data_dir, '*.wav')
		if not audio_files:
			raise ValueError("No WAV files found in '{}'.".format(self.data_dir))
		
		# if LC is enabled, check if local conditioning files exist
		if self.lc_enabled:
			lc_files = find_files(self.data_dir, self.lc_fileformat)
			if not lc_files:
				raise ValueError("No MIDI files found in '{}'".format(self.data_dir))


	def get_gc_cardinality(self):
		'''ADAPT:
			this is where we return the total number of unique GC embeddings
			for now, we do not have any particular scheme for GC'''
		return None


	def dq_audio(self, num_elements):
		'''Deques audio samples. Each element is an entire sample batch'''
		return self.q_audio.dequeue_many(num_elements)


	def dq_gc(self, num_elements):
		'''Deques corresponding GC embeddings samples. Each element is an entire sample batch'''
		return self.q_gc.dequeue_many(num_elements)

	
	def dq_lc(self, num_elements):
		'''Deques corresponding LC embeddings samples. Each element is an entire sample batch'''
		return self.q_lc.dequeue_many(num_elements)

	
	def input_stream(self):
		'''this is the main thread which gets the file names and GC embedding 
			and LC file name from the load_files
			and then pre-processes the audio for silence trimming (if enabled)
			and then up samples the local conditioning feeds them to the queues'''
		stop = False

		# keep looping until training is done
		while not stop:
			# get the list of files and related data
			iterator = load_files(self.data_dir, self.sample_rate, self.gc_enabled, self.lc_enabled, self.lc_fileformat)

			# ADAPT:
			# for MiDi LoCo, instatiate MidiMapper()
			if self.lc_enabled:
				mapper = MidiMapper(sample_rate = self.sample_rate,
									lc_channels = self.lc_channels)

			for audio, filename, gc_id, lc_timeseries in iterator:
				if self.coord.should_stop():
					stop = True
					break

				print(filename)
				# TODO: If we remove this silence trimming we can use the randomised queue
				# instead of the padding queue so that we dont have to take care of midi with silence
				if self.silence_threshold is not None:
					audio = trim_silence(audio[:, 0], self.silence_threshold)
					audio = audio.reshape(-1, 1)

					# now check if the whole audio was trimmed away
					if audio.size == 0:
						print("Warning: {} was ignored as it contains only "
							  "silence. Consider decreasing trim_silence "
							  "threshold, or adjust volume of the audio."
							  .format(filename))
						continue

				len_audio_prepad = len(audio)
				# now pad beginning of samples with n = receptive_ field number of 0s 
				# TODO: figure out why we are padding this ???
				audio = np.pad(audio, [[self.receptive_field, 0], [0, 0]], 'constant')
				len_audio_postpad = len(audio)

				# CHOP UP AUDIO
				if self.sample_size:
					#first_loop = True
					# TODO: sample size has been disabled for now. adapt to support in the future
					if self.lc_enabled:
						# ADAPT:
						# setup parametrs for MidiMapper
						previous_end = 0
						new_end = self.sample_size + self.receptive_field - 1
						mapper.set_midi(lc_timeseries)
						#np.zeros(shape = (len_audio_postpad - len_audio_prepad, self.lc_channels), dtype = np.float32)
						
					# TODO: understand the reason for this piece voodoo from the original reader
						lc_embeddings = mapper.upsample(start_sample = - self.receptive_field)

					while len(audio) > self.receptive_field:
						piece = audio[:(self.receptive_field + self.sample_size), :]
						self.sess.run(self.enq_audio, feed_dict = {self.audio_placeholder : piece})

						# add GC mapping to q if enabled
						if self.gc_enabled:
							self.sess.run(self.enq_gc, feed_dict = {self.gc_placeholder : gc_id})

						# add LC mapping to queue if enabled
						if self.lc_enabled:
							# TODO: sanity check the following four lines
							# mapper.set_sample_range(start_sample = previous_end - self.receptive_field, end_sample = new_end)
							# lc_encode = mapper.upsample(start_sample = previous_end - self.receptive_field, end_sample = new_end)
							# if (first_loop):
							#	first_pad = np.zeros(shape = (len_audio_postpad - len_audio_prepad, self.lc_channels), dtype = np.float32)
							#	lc_encode = np.concatenate((first_pad, lc_encode), axis = 0)
							#	first_loop = False
							lc_embeddings_chunk = lc_embeddings[:(self.receptive_field + self.sample_size), :]

							# now pad the embeddings to match size
							delta_len = len(piece) - len(lc_embeddings_chunk)
							if (delta_len > 0):
								lc_encode_postpad = np.zeros(shape = (delta_len, self.lc_channels), dtype = np.float32)
								lc_embeddings_chunk = np.concatenate((lc_embeddings_chunk, lc_encode_postpad), axis = 0)
							elif (delta_len < 0):
								lc_embeddings_chunk = lc_embeddings_chunk[0:len(lc_embeddings_chunk) + delta_len :1]
							
							self.sess.run(self.enq_lc, feed_dict = {self.lc_placeholder : lc_embeddings_chunk})
							# after queueing, shift audio frame to the next one
							previous_end = new_end
							new_end = new_end + self.sample_size
							
							print(delta_len)
							
						audio = audio[self.sample_size:, :]
						lc_embeddings = lc_embeddings[self.sample_size:, :]
							
						
						
				# DONT CHOP UP AUDIO
				else:
					# otherwise feed the whole audio sample in its entireity
					self.sess.run(self.enq_audio, feed_dict = {self.audio_placeholder : audio})

					# add GC mapping to q if enabled
					if self.gc_enabled:
						self.sess.run(self.enq_gc, feed_dict = {self.gc_placeholder : gc_id})
					
					# add LC mapping to queue if enabled
					if self.lc_enabled:
						# first we include the zero embeddings to compensate for the padding of the audio
						lc_encode_prepad = np.zeros(shape = (len_audio_postpad - len_audio_prepad, self.lc_channels), dtype = np.float32)
						# ADAPT:
						# first we pass the get the metadata to pass to the midi mapper
						mapper.set_sample_range(start_sample = 0, end_sample = len(audio))
						mapper.set_midi(lc_timeseries)
						lc_encode = mapper.upsample()
						lc_encode = np.concatenate((lc_encode_prepad, lc_encode), axis = 0)

						delta_len = len(audio) - len(lc_encode)
						if (delta_len > 0):
							lc_encode_postpad = np.zeros(shape = (delta_len, self.lc_channels), dtype = np.float32)  
							lc_encode = np.concatenate((lc_encode, lc_encode_postpad), axis = 0)
						elif (delta_len < 0):
							lc_encode = lc_encode[0:len(lc_encode) + delta_len - 1:1]
						
						
						self.sess.run(self.enq_lc, feed_dict = {self.lc_placeholder : lc_encode})


	def start_threads(self, n_threads = 1):
		for _ in range(n_threads):
			thread = threading.Thread(target = self.input_stream, args = ())
			thread.daemon = True  # Thread will close when parent quits.
			thread.start()
			self.threads.append(thread)
		return self.threads

# Template for the midi mapper
class MidiMapper():
	
	def __init__(self,
				 sample_rate = 16000,
				 lc_channels = 128):
		# input variabels
		self.sample_rate = sample_rate
		self.lc_channels = lc_channels

		# self.tempo IS THE SAME AS microseconds per beat: default is 120 BPM = 500000 usecs per beat
		# self.PPQN IS THE SAME AS resolution of the midi
		self.start_sample = None
		self.end_sample = None
		self.tempo = None
		self.PPQN = None
		self.first_note_index = None
		self.midi = None

		# mapper just holds a queue of numpy arrays, shape of the overall queue is (1, lc_channels)
		self.mapper_lc_q = queue.Queue()

	def set_sample_range(self, start_sample, end_sample):
		'''Allow the sample range to change at runtime so new MidiMappers'''
		
		self.start_sample = start_sample
		self.end_sample = end_sample


	def set_midi(self, midi):
		'''Allow midi file to be reassigned at runtime so that new MidiMappers
		   do not have to be instantiated for the each new midi file'''
		self.midi = midi
		self.update_midi_metadata()


	def sample_to_microseconds(self, sample_num):
		'''takes in a sample number of the wav and the sample rate and 
			gets the corresponding microseconds of the sample in the song'''
		return (int(1e6) / self.sample_rate) * sample_num
		
		
	def tick_delta_to_microseconds(self, delta_ticks):
		'''converts a range of midi ticks into a range of microseconds'''
		return (((self.tempo * delta_ticks) / self.PPQN))
	
		
	def microseconds_per_tick(self):
		'''takes in the tempo and the resolution and outputs the number of microseconds per tick'''
		return ((self.tempo / self.PPQN))
	
	
	def update_midi_metadata(self):
		'''gets all the metadata here from the midi file header'''
		tempo = None
		track = self.midi[0]
		event_name = track[0].name
		first_note_index = 0
		
		# we want the tempo in microsec/beat - the set tempo events set the tempo as tt tt tt - 
		# 24-bit binary representing microseconds (time) per beat 
		# (instead of beat per time/BPM)

		# this is getting the index of first note event in the midi to ignore all other BS
		# and also the tempo hehe
		event_name = track[first_note_index].name
		while event_name is not midi.NoteOnEvent.name:
			if event_name is midi.SetTempoEvent.name:
				# indicating a tempo is set before the first note as initial tempo
				# get the 24-bit binary as a string
				tempo_binary = (format(track[first_note_index].data[0], '08b')+
								format(track[first_note_index].data[1], '08b')+
								format(track[first_note_index].data[2], '08b'))
				
				# convert the index string to microsec/beat
				tempo = int(tempo_binary, 2)
				
			first_note_index += 1
			event_name = track[first_note_index].name

 		# this the tempo for the first note of the midi
		self.tempo = tempo if tempo is not None else 500000

		# this is the PPQ (pulses per quarter note, aka ticks per beat). Constant.
		self.PPQN = self.midi.resolution

		# this is the index in the track for the first note of midi
		self.first_note_index = first_note_index

		
		
	def enq_embeddings(self, delta_ticks, note_state):
		'''takes in the notes to be upsampled as a state array and the time to be upsampled for 
		and then upsamples the notes according to the wav sampling rate, makes embeddings and adds them  
		to the tf queue''' 
		upsample_time = self.tick_delta_to_microseconds(delta_ticks)

		# TODO: figure out if batching all  inserts from the loops into a giant block
		# of inserts will be more efficient if used with enqueue_many
		num_embeddings = upsample_time * self.sample_rate / 1000000

		for i in range(num_embeddings - 1):
			# an empty embedding which is the intialzation for each elemnt of the mapper queue
			# we cannot use a single empty variable and assign 1s to it as all array variables are handles and not literals
			embedding = np.zeros(shape = (self.lc_channels), dtype = np.float32)

			# single line for loop that encodes the embedding according the state vector of the notes
			for j in range(len(note_state)):
				embedding[note_state[j - 1]] = 1
			
			# embeddign made, now enqueue
			self.mapper_lc_q.put(embedding)

	
	def upsample(self, start_sample = 0, end_sample = None):
		
		# stores the current state of the midi: ie. which notes are on 
		note_state = []

		# input midi is the midi pattern, the output of read_midifile. Assume its format and get the first track of the midi
		# This track is a list of events occurring in the midi
		midi_track = self.midi[0]
		
		# First get the start and end times of the midi section to be extracted and upsampled
		current_time = self.sample_to_microseconds(np.maximum(0,start_sample))

		if end_sample is None:
			end_time = float('inf')
		else:
			end_time = self.sample_to_microseconds(end_sample)
		# print("Ending time for upsample is {}".format(end_time))

		counter = self.first_note_index
		# print("First note index is {}".format(counter))
		
		while current_time < end_time:
			# first get the current midi event
			curr_event = midi_track[counter]
			# print("Counter : {}".format(counter))
			# print("Current event = {}".format(curr_event.name))
			# extract the time tick deltas and the event types form the midi
			delta_ticks = curr_event.tick
			event_name  = curr_event.name
			event_data  = curr_event.data
			
			if   event_name is midi.NoteOnEvent.name  and delta_ticks == 0:
				note_state.append(event_data[0])
				
			elif event_name is midi.NoteOnEvent.name  and delta_ticks != 0:
				self.enq_embeddings(delta_ticks, note_state)
				note_state.append(event_data[0])
				
			elif event_name is midi.NoteOffEvent.name and delta_ticks == 0:
				if event_data[0] in note_state:
					note_state.remove(event_data[0])
				
			elif event_name is midi.NoteOffEvent.name and delta_ticks != 0:
				self.enq_embeddings(delta_ticks, note_state)
				if event_data[0] in note_state:
					note_state.remove(event_data[0])
				
			elif event_name is midi.EndOfTrackEvent.name:
				# warn if gap between midi and wav, then update time
				# the embedding is already zero-padded, so no need to pad it
				# get the bpm and find how many seconds for one beat and then half that
				if (end_time - current_time) > (self.PPQN / 2):
					# the MIDI ended, but the .wav sample hasn't reached its end
					# print("The given .wav file is longer than the matching MIDI file. Please check that the MIDI and .wav line up correctly.")
					break # to break outer while loop
				else:
					break # if not already, to break outer while loop

			elif event_name is midi.SetTempoEvent.name and delta_ticks == 0:
				# mid-song tempo change
				# tempo is represented in microseconds per beat as tt tt tt - 24-bit (3-byte) hex
				# convert first to binary string and then to a decimal number (microsec/beat)
				tempo_binary = (format(curr_event.data[0], '08b')+
								format(curr_event.data[1], '08b')+
								format(curr_event.data[2], '08b'))
				self.tempo = int(tempo_binary, 2)
				
			elif event_name is midi.SetTempoEvent.name and delta_ticks != 0:
				self.enq_embeddings(delta_ticks, note_state)
				tempo_binary = (format(curr_event.data[0], '08b')+
								format(curr_event.data[1], '08b')+
								format(curr_event.data[2], '08b'))
				self.tempo = int(tempo_binary, 2)
				
				
			else:
				# We are ignoring events other than note on/off or tempo. Do nothing with these events.
				print("Unrecognised MIDI event {}".format(event_name))
				

			# increment
			current_time += self.tick_delta_to_microseconds(delta_ticks)
			if (current_time < end_time):
				counter += 1
			# print("Current time is {}".format(current_time))


		# save current midi track pointer in case the song is cunked up
		self.first_note_index = counter
		
		# current_time = end_time, but the MIDI isn't at the end of the track yet
		#if midi_track[counter].name is not "End of Track":
		#	print("The given MIDI file is longer than the matching .wav file. Please check that the MIDI and .wav line up correctly.")
			# then continue like it isn't our fault

	
		# now return all indicidual embeddings as one list
		
		num_embeddings = self.mapper_lc_q.qsize()
		embedding_vector = np.zeros(shape = (self.mapper_lc_q.qsize(), self.lc_channels), dtype = np.float32)

		for i in range(num_embeddings - 1):
			embedding_vector[i] = self.mapper_lc_q.get()

		# pack all individual embeddings along the 0th dimention into one tensor
		# this is a colum vector of row vectors
		# colum is num_embeddings rows tall
		# each row in this column self.lc_channels float32s wide
		if start_sample < 0:
			padding = np.zeros(shape = (np.abs(start_sample), self.lc_channels), dtype = np.float32)
			embedding_vector = np.concatenate((padding, embedding_vector), axis = 0)
		 
		return embedding_vector
