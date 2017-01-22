#!/usr/bin/env python

import sys
import zipfile
import XML
import struct
import ctypes
import math
import datetime

TOTAL_SEMITONES = 120
SAMPLERATE = 44100

class InputException(Exception):
	def __init__(self, message):
		Exception.__init__(self)
		self.message = message


class Volume:
	def __init__(self, left, right):
		self.left = left
		self.right = right

	def __mul__(self, other):
		return Volume(self.left * other.left, self.right * other.right)

	def __eq__(self, other):
		return self.left == other.left and self.right == other.right

	def isPanned(self):
		return self.left != self.right

def makeVolume(xvolume):
	v = float(xvolume)
	return Volume(v,v)

def makePanning(xpanning):
	p = float(xpanning)
	return Volume(math.sqrt(2.0 * (1.0 - p)), math.sqrt(2.0 * p))


class Instrument:
	NAMES = ["seed","modes","fat","width",
			 "overtones","sharpness","harmonicity","decaylow","decayhigh",
			 "filterlow","fslopelow","filterhigh","fslopehigh","fsweep",
			 "gain","attack","release","stereo",
			 "dummy1", "dummy2",
			 "q_decaydiff", "q_decaylow", "q_harmonicity", "q_sharpness", "q_width",
			 "q_f_low", "q_fs_low", "q_f_high", "q_fs_high", "q_fsweep",
			 "q_gain", "q_attack", "q_release"
	]

	def __init__(self, number, name, params):
		names = Instrument.NAMES
		self.number = number
		self.name = name
		self.params = (params + [0.0] * len(names))[:len(names)]
		for i,p in enumerate(self.params):
			self.__dict__[names[i]] = p
		self.volume = Volume(1.0, 1.0)
		self.maxsamples = 0
		self.title = "%02X|%s" % (self.number, self.name)


class Note:
	NOTEBASES = {
		"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11
	}

	NOTENAMES = {
		0: "C-", 1: "C#", 2: "D-", 3: "D#", 4: "E-", 5: "F-",
		6: "F#", 7: "G-", 8: "G#", 9: "A-", 10: "A#", 11: "B-"
	}

	def __init__(self, tname, line, songpos, pat, patline, note, instr, velocity):
		note = str(note)
		self.line = int(line)
		self.songpos = int(songpos)
		self.pat = int(pat)
		self.patline = patline
		if note == "OFF":
			self.off = True
			self.tone = None
			self.instr = 0
			self.velocity = 0
		else:
			self.off = False
			octave = int(note[2])
			notebase = Note.NOTEBASES[note[0]]
			sharp = int(note[1] == "#")
			self.tone = octave * 12 + notebase + sharp
			self.instr = int(str(instr), 16)
			try:
				self.velocity = 127 if str(velocity) == "" or str(velocity) == ".." else int(str(velocity), 16)
			except ValueError:
				print "Track '%s' uses illegal velocity value '%s' at pattern %d line %d" % (tname, str(velocity), pat, patline);
				self.velocity = 127

def notename(tone):
	return Note.NOTENAMES[tone%12] + str(tone/12)


def instplugins(xinst):
	xplugins = xinst.PluginProperties
	if xplugins:
		return xplugins
	return xinst.PluginGenerator

def isactive(xdevice):
	if not xdevice:
		return False
	if xdevice.IsActive.Value:
		return float(xdevice.IsActive.Value) != 0.0
	else:
		return str(xdevice.IsActive) == "true"


def quantize(value, level):
	bit = 1 << int(math.floor(level * 31))
	mask = 0x100000000 - bit
	add = bit >> 1
	i = struct.unpack('I', struct.pack('f', value))[0]
	i = (i + add) & mask
	if i == 0x80000000:
		i = 0x00000000
	q = struct.unpack('f', struct.pack('I', i))[0]
	return q

def makeParamBlock(inst, uses_panning):
	modes = max(1, math.floor(0.5 + inst.modes * 100))
	fat = max(1, math.floor(0.5 + inst.fat * 100))
	seed = math.floor(0.5 + inst.seed * 100)
	overtones = math.floor(0.5 + inst.overtones * 100)
	decaydiff = inst.decayhigh - inst.decaylow
	decaylow = inst.decaylow
	harmonicity = inst.harmonicity * 2 - 1
	sharpness = inst.sharpness * 5 - 4
	width = 100 * math.pow(inst.width, 5)

	fsweep = -math.pow(inst.fsweep - 0.5, 3) * 100 * TOTAL_SEMITONES / SAMPLERATE
	fslopelow = math.pow((1 - inst.fslopelow), 3)
	fslopehigh = -math.pow((1 - inst.fslopehigh), 3)
	filterlow = (inst.filterlow * 2 - 1) * TOTAL_SEMITONES
	filterhigh = (inst.filterhigh * 2 - 1) * TOTAL_SEMITONES

	gain = math.pow(4096, inst.gain - 0.25)
	attack = 2.0 if inst.attack == 0.0 else 1 / (inst.attack * inst.attack) / SAMPLERATE
	release = -(2.0 if inst.release == 0.0 else 1 / inst.release / SAMPLERATE)

	decaydiff = quantize(decaydiff, inst.q_decaydiff)
	decaylow = quantize(decaylow, inst.q_decaylow)
	harmonicity = quantize(harmonicity, inst.q_harmonicity)
	sharpness = quantize(sharpness, inst.q_sharpness)
	width = quantize(width, inst.q_width)
	filterlow = quantize(filterlow, inst.q_f_low)
	fslopelow = quantize(fslopelow, inst.q_fs_low)
	filterhigh = quantize(filterhigh, inst.q_f_high)
	fslopehigh = quantize(fslopehigh, inst.q_fs_high)
	fsweep = quantize(fsweep, inst.q_fsweep)
	gain = quantize(gain, inst.q_gain)
	attack = quantize(attack, inst.q_attack)
	release = quantize(release, inst.q_release)

	maxdecay = max(decaylow, decaylow + decaydiff)
	releasetime = inst.maxtime * SAMPLERATE + 1.0 / -release if release != 0.0 else float('inf')
	decaytime = (math.log(0.01, maxdecay)) * 4096 if maxdecay < 1.0 else float('inf')
	if math.isinf(releasetime) and math.isinf(decaytime):
		raise InputException("Instrument '%s' has infinite duration" % inst.title)
	inst.maxsamples = int(min(releasetime, decaytime) + 65535) & -65536

	left_volume = inst.volume.left * inst.velocity_quantum * 128
	right_volume = inst.volume.right * inst.velocity_quantum * 128
	volume = (left_volume + right_volume) / 2
	pan = right_volume / volume - 1
	volume = quantize(volume, 0.65)
	pan = quantize(pan, 0.55)

	return [int(modes), int(fat), int(seed), int(overtones),
			decaydiff, decaylow, harmonicity, sharpness, width,
			filterlow, fslopelow, filterhigh, fslopehigh, fsweep,
			gain, inst.maxsamples, attack, release,
			volume] + ([pan] if uses_panning else [])


class Track:
	def __init__(self, number, column, name, notes, volume, instruments):
		self.number = number
		self.column = column
		self.name = name
		self.notes = notes
		self.volume = volume
		self.notemap = dict()
		self.tav_repr = dict()
		self.note_lengths = dict()

		self.title = "%s, column %d" % (self.name, self.column)

		self.labelname = ""
		for c in self.name:
			if c.isalnum() or c == '_':
				self.labelname += c

		self.instr = None
		self.max_length = 0

		prev = None
		for note in notes:
			if prev is not None and not prev.off:
				if prev.instr is None:
					raise InputException("Track '%s' uses undefined instrument at pattern %d line %d" % (name, prev.pat, prev.patline));
				if self.instr is not None and prev.instr != self.instr:
					raise InputException("Track '%s' has more than one instrument at pattern %d line %d" % (name, prev.pat, prev.patline))
				self.instr = prev.instr
				length = note.line - prev.line
				if length < 0:
					raise InputException("Track '%s' has reversed note order from %d to %d" % (name, prev.patline, note.patline))
				if prev.tone is None:
					raise InputException("Track '%s' has a toneless note at %d" % (name, prev.patline))
				self.max_length = max(self.max_length, length)
				tav = (prev.tone, prev.velocity)
				self.notemap[prev] = tav

				if length not in self.note_lengths:
					self.note_lengths[length] = 0
				self.note_lengths[length] += 1

			prev = note

		if not prev.off:
			 raise InputException("Track '%s' is not terminated." % name)

		if len(self.note_lengths) == 1:
			for l in self.note_lengths:
				self.singular_length = l
		else:
			self.singular_length = None

		self.tavs = sorted(set(self.notemap.values()), key = (lambda (t,v) : (t,v)))
		for i,tav in enumerate(self.tavs):
			if tav[0] is None:
				raise InputException("Track '%s' has a toneless note" % name)
			self.tav_repr[tav] = i

		self.longest_sample = None
		self.sample_length_sum = None


class Music:
	def __init__(self, tracks, instruments, length, ticklength, n_delay_tracks, delay_lengths, delay_strength, master_volume):
		self.tracks = tracks
		self.instruments = instruments
		self.length = length
		self.ticklength = ticklength
		self.n_delay_tracks = n_delay_tracks
		self.delay_lengths = delay_lengths
		self.delay_strength = delay_strength
		self.master_volume = master_volume

		# Calculate track order and velocity set
		self.track_order = []
		self.uses_panning = False
		for ii,instr in enumerate(self.instruments):
			if instr is None:
				continue
			velocities = set()
			instr.columns = 0
			volume = None
			for ti,track in enumerate(self.tracks):
				if track.instr == ii:
					self.track_order.append(ti)
					instr.columns += 1
					v = track.volume * self.master_volume
					if volume is not None and not v == volume:
						raise InputException("Track '%s' has different volume/panning than previous tracks with same instrument" % track.title)
					volume = v
					for t,v in track.tavs:
						velocities.add(v)
			instr.volume *= volume
			instr.velocities = sorted(list(velocities))
			if instr.volume.isPanned():
				self.uses_panning = True

			quantum = 128
			while quantum > 1:
				if all(v == 127 or v % quantum == 0 for v in instr.velocities):
					break
				quantum /= 2
			instr.velocity_quantum = quantum

		# Calculate longest sample
		self.max_maxsamples = 0
		self.max_total_samples = 0
		for ii,instr in enumerate(self.instruments):
			if instr is None:
				continue
			instr.maxtime = 0
			tones = set()
			for ti,track in enumerate(self.tracks):
				if track.instr != ii:
					continue
				instr.maxtime = max(instr.maxtime, track.max_length * ticklength)
				for note in track.notes:
					if note.tone is not None:
						tones.add(note.tone)

			instr.tones = sorted(list(tones))
			instr.tonemap = dict()
			for i,t in enumerate(instr.tones):
				instr.tonemap[t] = i

			instr.paramblock = makeParamBlock(instr, self.uses_panning)

			self.max_maxsamples = max(self.max_maxsamples, instr.maxsamples)
			self.max_total_samples = max(self.max_total_samples, instr.maxsamples * len(instr.tones))

		self.datainit = None
		self.out = None

	def dataline(self, data):
		if len(data) > 0:
			line = self.datainit
			first = True
			for d in data:
				if not first:
					line += ","
				line += str(d)
				first = False
			line += "\n"
			self.out += line

	def comment(self, c):
		self.out += "\t; %s\n" % c

	def label(self, l):
		self.out += "%s:\n" % l

	def notelist(self, datafunc, trackterm, prefix):
		for ti in self.track_order:
			track = self.tracks[ti]
			self.comment(track.title)
			self.label("%s%s_%d" % (prefix, track.labelname, track.column))
			prev_n = None
			pat_data = []
			for n in track.notes:
				if track.singular_length and n.off and n.line > 0:
					continue
				if prev_n is not None:
					pat_data += datafunc(track,prev_n,n)
				if not n.off if prev_n is None else n.songpos != prev_n.songpos:
					self.dataline(pat_data)
					pat_data = []
					self.comment("Position %d, pattern %d" % (n.songpos, n.pat))
				prev_n = n
			pat_data += datafunc(track,prev_n,None)
			self.dataline(pat_data)
			self.dataline(trackterm)
			self.out += "\n"

	def lendata(self, t, pn, n):
		if n is None:
			return []
		step = n.line-pn.line
		return [-1 - (step >> 8), step & 255] if step > 127 else [step]

	def samdata(self, t, pn, n):
		if pn.off:
			return [0]
		return [1 + t.tav_repr[t.notemap[pn]]]

	def export(self):
		self.datainit = "\tdb\t"
		self.out = ""

		spt = int(self.ticklength * SAMPLERATE)

		def roundup(v):
			return (int(v) & -0x10000) + 0x10000

		num_instruments = sum(1 for instr in self.instruments if instr is not None)

		global infile
		self.out += "; Music converted from %s %s\n" % (infile, str(datetime.datetime.now())[:-7])
		self.out += "\n"
		self.out += "%%define SAMPLES_PER_TICK %d\n" % spt
		self.out += "%%define MAX_TOTAL_INSTRUMENT_SAMPLES %d\n" % roundup(self.max_total_samples)
		self.out += "%%define TOTAL_SAMPLES %d\n" % roundup((self.length * self.ticklength) * SAMPLERATE + self.max_maxsamples)
		self.out += "\n"
		#self.out += "%%define MAX_DELAY_LENGTH %d\n" % int(max(self.delay_lengths) * SAMPLERATE)
		#self.out += "%%define LEFT_DELAY_LENGTH %d\n" % int(self.delay_lengths[0] * SAMPLERATE)
		#self.out += "%%define RIGHT_DELAY_LENGTH %d\n" % int(self.delay_lengths[1] * SAMPLERATE)
		#self.out += "%%define DELAY_STRENGTH %0.8f\n" % self.delay_strength
		#self.out += "\n"
		self.out += "%%define NUMTRACKS %d\n" % num_instruments
		#self.out += "%%define LOGNUMTICKS %d\n" % int(math.log(self.length, 2) + 1)
		self.out += "%%define MUSIC_LENGTH %d\n" % self.length
		self.out += "%%define TICKS_PER_SECOND %0.8f\n" % (1.0 / self.ticklength)

		if self.uses_panning:
			self.out += "\n%define USES_PANNING\n"

		# Instrument parameters
		self.out += "\n\n\tsection iparam data align=4\n"
		self.out += "\n_InstrumentParams:\n"
		for ii,instr in enumerate(self.instruments):
			if instr is None:
				continue
			self.label(".i%02d" % ii)
			self.comment(instr.title)
			self.out += "\tdd\t"
			first = True
			for p in instr.paramblock:
				if not first:
					self.out += ","
				if isinstance(p, float):
					self.out += "0x%08X" % struct.unpack('I', struct.pack('f', p))[0]
				else:
					self.out += "%d" % p
				first = False
			self.out += "\n"
		self.out += "\n"

		# Instrument tones
		self.out += "\n\n\tsection itones data align=1\n"
		self.out += "\n_InstrumentTones:\n"
		for ii,instr in enumerate(self.instruments):
			if instr is None:
				continue
			self.label(".i%02d" % ii)
			self.comment(instr.title)
			self.out += "\tdb\t"
			prev_tone = 0
			for tone in instr.tones:
				self.out += str(tone - prev_tone) + ","
				prev_tone = tone
				first = False
			self.out += "%d\n" % (-129 + instr.columns)

		# Track data
		self.out += "\n\n\tsection trdata data align=1\n"
		self.out += "\n_TrackData:\n"
		for ti in self.track_order:
			track = self.tracks[ti]
			instr = self.instruments[track.instr]

			#if self.n_delay_tracks > 0 and ti == self.n_delay_tracks:
			#	self.dataline([-1])
			self.label(".t_%s_%d" % (track.labelname, track.column))
			self.comment(track.title)

			# List tones and velocities
			tavdata = [track.singular_length if track.singular_length else 0]
			prev_tone_id = 0
			for t,v in track.tavs:
				tone_id = instr.tonemap[t]
				vol = (v + instr.velocity_quantum / 2) / instr.velocity_quantum
				tavdata += [tone_id - prev_tone_id, vol]
				prev_tone_id = tone_id
			tavdata += [-128]
			self.dataline(tavdata)

		# Lengths of notes
		self.out += "\n\tsection notelen data align=1\n"
		self.out += "\n_NoteLengths:\n"
		self.notelist(self.lendata, [0], "L_")

		# Samples for notes
		self.out += "\n\tsection notesamp data align=1\n"
		self.out += "\n_NoteSamples:\n"
		self.notelist(self.samdata, [], "S_")

		return self.out

	def makeDeltas(self, init_delta, lines_per_beat):
		beats_per_line = 1.0/lines_per_beat
		deltas = []
		for t in self.tracks:
			tdeltas = []
			delta = init_delta
			note_i = 0
			for p in range(0, self.length):
				while t.notes[note_i].line <= p:
					if not t.notes[note_i].off:
						delta = p * beats_per_line
					note_i += 1
				tdeltas.append(delta)
			deltas.append(tdeltas)
		return deltas


def extractTrackNotes(xsong, tr, col):
	outside_pattern = 0
	xsequence = xsong.PatternSequence.PatternSequence
	if not xsequence:
		xsequence = xsong.PatternSequence.SequenceEntries.SequenceEntry
	xpatterns = xsong.PatternPool.Patterns.Pattern
	tname = str(xsong.Tracks.SequencerTrack[tr].Name)

	notes = []

	pattern_top = 0
	prev_instr = None
	for posn,xseq in enumerate(xsequence):
		patn = int(xseq.Pattern)
		xpat = xpatterns[patn]
		nlines = int(xpat.NumberOfLines)
		if tr in [int(xmt) for xmt in xseq.MutedTracks.MutedTrack]:
			off = Note(tname, pattern_top, posn, patn, 0, "OFF", None, 127)
			notes.append(off)
		else:
			xtrack = xpat.Tracks.PatternTrack[tr]
			for xline in xtrack.Lines.Line:
				index = int(xline("index"))
				if index < nlines:
					line = pattern_top + index
					xcol = xline.NoteColumns.NoteColumn[col]
					if xcol.Note and str(xcol.Note) != "---":
						instr = str(xcol.Instrument)
						if instr == ".." and str(xcol.Note) != "OFF":
							if prev_instr is None:
								raise InputException("Track '%s' pattern %d position %d: Unspecified instrument" % (tname, patn, index))
							instr = prev_instr
						prev_instr = instr

						note = Note(tname, line, posn, patn, index, xcol.Note, instr, xcol.Volume)
						notes.append(note)

						if (note.velocity == 0 or note.velocity > 127) and not note.off:
							raise InputException("Track '%s' pattern %d position %d: Illegal velocity value" % (tname, patn, index))

					# Check for illegal uses of panning, delay and effect columns
					def checkColumn(x, allow_zero, msg):
						if x and not (str(x) == "" or str(x) == ".." or (allow_zero and str(x) == "00")):
							raise InputException("Track '%s' pattern %d position %d: %s" % (tname, patn, index, msg))
					checkColumn(xcol.Panning, False, "Panning column used")
					checkColumn(xcol.Delay, True, "Delay column used")
					for xeff in xline.EffectColumns.EffectColumn.Number:
						checkColumn(xeff, True, "Effect column used")
				else:
					outside_pattern += 1
		pattern_top += nlines
	notes.append(Note(tname, pattern_top, len(xsequence), len(xpatterns), 0, "OFF", 0, 127))

	# Add inital OFF and remove redundant OFFs
	if notes[0].line == 0:
		notes2 = []
		off = False
	else:
		notes2 = [Note(tname, 0, 0, int(xsequence[0].Pattern), 0, "OFF", 0, 127)]
		off = True
	for n in notes:
		if n.off:
			if not off:
				notes2.append(n)
				off = True
		else:
			notes2.append(n)
			off = False

	if outside_pattern > 0:
		print " * Track '%s': %d note%s outside patterns ignored" % (tname, outside_pattern, "s" * (outside_pattern > 1))

	return notes2

def pickupDelay(xdevices, delay_lengths, delay_strength, tname, ticklength):
	if isactive(xdevices.DelayDevice):
		send = float(xdevices.DelayDevice.TrackSend.Value) / 127.0
		lfeedback = float(xdevices.DelayDevice.LFeedback.Value)
		rfeedback = float(xdevices.DelayDevice.RFeedback.Value)
		if float(xdevices.DelayDevice.LineSync.Value):
			lsynctime = float(xdevices.DelayDevice.LSyncTime.Value)
			lsyncoffset = float(xdevices.DelayDevice.LSyncOffset.Value)
			ldelay = (lsynctime + lsyncoffset) * ticklength
			rsynctime = float(xdevices.DelayDevice.RSyncTime.Value)
			rsyncoffset = float(xdevices.DelayDevice.RSyncOffset.Value)
			rdelay = (rsynctime + rsyncoffset) * ticklength
		else:
			ldelay = float(xdevices.DelayDevice.LDelay.Value) / 1000.0
			rdelay = float(xdevices.DelayDevice.RDelay.Value) / 1000.0
		if abs(lfeedback - send) > 0.05:
			print " * Track '%s': Left feedback (%0.2f) is different from send value (%0.2f)" % (tname, lfeedback, send)
		if abs(rfeedback - send) > 0.05:
			print " * Track '%s': Right feedback (%0.2f) is different from send value (%0.2f)" % (tname, rfeedback, send)
		if delay_lengths != [0.0, 0.0] and ([ldelay,rdelay] != delay_lengths or send != delay_strength):
			print " * Track '%s' has different delay parameters from earlier track" % tname
		return [ldelay,rdelay],send
	return delay_lengths,delay_strength

def makeTracks(xsong, ticklength):
	instruments = []
	delay_tracks = []
	non_delay_tracks = []
	delay_lengths = [0.0, 0.0]
	delay_strength = 0.0

	for ii,xinst in enumerate(xsong.Instruments.Instrument):
		params = [float(v) for v in instplugins(xinst).PluginDevice.Parameters.Parameter.Value]
		if params:
			instrument = Instrument(ii, str(xinst.Name), params)
			instrument.volume = makeVolume(instplugins(xinst).Volume)
			instruments.append(instrument)
			
		else:
			instruments.append(None)

	for tr,xtrack in enumerate(xsong.Tracks.SequencerTrack):
		tname = str(xtrack.Name)
		ncols = int(xtrack.NumberOfVisibleNoteColumns)
		xdevices = xtrack.FilterDevices.Devices
		xdevice = xdevices.SequencerTrackDevice
		if not xdevice:
			xdevice = xdevices.TrackMixerDevice
		volume = makeVolume(xdevice.Volume.Value)
		volume *= makePanning(xdevice.Panning.Value)
		while isactive(xdevices.SendDevice):
			if isactive(xdevices.DelayDevice):
				raise InputException("Track '%s' uses both delay and send" % tname);
			if str(xdevices.SendDevice.MuteSource) != "true":
				raise InputException("Track '%s' uses send without Mute Source" % tname);
			volume *= makeVolume(xdevices.SendDevice.SendAmount.Value)
			volume *= makePanning(xdevices.SendDevice.SendPan.Value)
			dest = int(float(xdevices.SendDevice.DestSendTrack.Value))
			xdevices = xsong.Tracks.SequencerSendTrack[dest].FilterDevices.Devices
			xdevice = xdevices.SequencerSendTrackDevice
			if not xdevice:
				xdevice = xdevices.SendTrackMixerDevice
			volume *= makeVolume(xdevice.Volume.Value)
			volume *= makePanning(xdevice.Panning.Value)
		volume *= makeVolume(xdevice.PostVolume.Value)
		volume *= makePanning(xdevice.PostPanning.Value)

		for col in range(0,ncols):
			notes = extractTrackNotes(xsong, tr, col)

			track_instrs = []
			for note in notes:
				if not note.off:
					instr = instruments[note.instr]
					if instr is None:
						raise InputException("Track '%s' uses undefined instrument (%d)" % (tname, note.instr));
					if note.instr not in track_instrs:
						track_instrs.append(note.instr)

			for instr in track_instrs:
				track = Track(tr, col + 1, tname, notes, volume, instruments)
				if isactive(xdevices.DelayDevice):
					delay_tracks.append(track)
				else:
					non_delay_tracks.append(track)
	
		delay_lengths,delay_strength = pickupDelay(xdevices, delay_lengths, delay_strength, tname, ticklength)

	for xtrack in xsong.Tracks.SequencerSendTrack:
		xdevices = xtrack.FilterDevices.Devices
		if xdevices.DelayDevice:
			delay_lengths,delay_strength = pickupDelay(xdevices, delay_lengths, delay_strength, tname, ticklength)

	#delay_tracks = sorted(delay_tracks, key = (lambda t : t.instr))
	#non_delay_tracks = sorted(non_delay_tracks, key = (lambda t : t.instr))

	return (delay_tracks + non_delay_tracks), len(delay_tracks), delay_lengths, delay_strength, instruments

def makeMusic(xsong):
	xgsd = xsong.GlobalSongData
	if xgsd.PlaybackEngineVersion and int(xgsd.PlaybackEngineVersion) >= 4:
		lines_per_minute = float(xgsd.BeatsPerMin) * float(xgsd.LinesPerBeat)
		print "New timing format: %d ticks per minute" % lines_per_minute
	else:
		lines_per_minute = float(xgsd.BeatsPerMin) * 24.0 / float(xgsd.TicksPerLine)
		print "Old timing format: %d ticks per minute" % lines_per_minute
	ticklength = 60.0 / lines_per_minute
	print

	tracks,n_delay_tracks,delay_lengths,delay_strength,instruments = makeTracks(xsong, ticklength)

	xpositions = xsong.PatternSequence.PatternSequence.Pattern
	if not xpositions:
		xpositions = xsong.PatternSequence.SequenceEntries.SequenceEntry.Pattern
	xpatterns = xsong.PatternPool.Patterns.Pattern
	length = 0
	for xpos in xpositions:
		patn = int(xpos)
		xpat = xpatterns[patn]
		nlines = int(xpat.NumberOfLines)
		length += nlines

	xmstdev = xsong.Tracks.SequencerMasterTrack.FilterDevices.Devices.SequencerMasterTrackDevice
	if not xmstdev:
		xmstdev = xsong.Tracks.SequencerMasterTrack.FilterDevices.Devices.MasterTrackMixerDevice
	master_volume = makeVolume(xmstdev.Volume.Value)
	master_volume *= makePanning(xmstdev.Panning.Value)
	master_volume *= makeVolume(xmstdev.PostVolume.Value)
	master_volume *= makePanning(xmstdev.PostPanning.Value)

	return Music(tracks, instruments, length, ticklength, n_delay_tracks, delay_lengths, delay_strength, master_volume)


def printMusicStats(music):
	print "Music length: %d ticks at %0.2f ticks per minute" % (music.length, 60.0 / music.ticklength)
	ii = None
	for ti in music.track_order:
		track = music.tracks[ti]
		if track.instr != ii:
			print
			ii = track.instr
			instr = music.instruments[ii]
			modes = instr.paramblock[0]
			fat = instr.paramblock[1]
			longest = float(instr.paramblock[15]) / SAMPLERATE
			burden = modes * fat * len(instr.tones) * longest
			print instr.title
			print " Burden:     modes x fat x tones x longest = %d x %d x %d x %.3f = %.f" % (modes, fat, len(instr.tones), longest, burden)
			tones = ""
			for t in instr.tones:
				tones += " " + notename(t)
			print " Tones:     " + tones
			velocities = ""
			for v in instr.velocities:
				velocities += " %02X" % v
			vbits = int(round(math.log(128 / instr.velocity_quantum, 2)))
			print " Velocities:" + velocities + " (%d bits)" % vbits

		print " " + track.title

		lengths = ""
		for l in sorted(track.note_lengths.keys()):
			lengths += " %d(%d)" % (l, track.note_lengths[l])
		print "  Lengths:  " + lengths

		tnotes = ""
		for t,v in track.tavs:
			num_notes = 0
			for n in [note for note in track.notes if not note.off and note.instr == track.instr]:
				if track.notemap[n] == (t,v):
					num_notes += 1
			tnotes += " %s/%02X(%d)" % (notename(t), v, num_notes)
		if music.n_delay_tracks > 0 and ti == 0:
			print "Tracks with delay:"
			print
		if music.n_delay_tracks > 0 and ti == music.n_delay_tracks:
			print
			print "Tracks without delay:"
			print
		print "  Notes:    " + tnotes


def writefile(filename, s):
	f = open(filename, "wb")
	f.write(s)
	f.close()
	print "Wrote file %s" % filename


if len(sys.argv) < 3:
	print "Usage: %s <input xrns file> <output asm file>" % sys.argv[0]
	sys.exit(1)

infile = sys.argv[1]
outfile = sys.argv[2]

x = XML.makeXML(zipfile.ZipFile(infile).read("Song.xml"))
try:
	music = makeMusic(x.RenoiseSong)
	print
	printMusicStats(music)
	print

	writefile(outfile, music.export())

	if len(sys.argv) > 4:
		deltas = music.makeDeltas(0.0, 1.0)
		syncfile = sys.argv[3]
		header = ""
		header += struct.pack('I', 1)
		header += struct.pack('I', music.length*4)
		header += struct.pack('I', len(music.tracks)*music.length*4)
		body = ""
		for t,tdeltas in enumerate(deltas):
			body += struct.pack("%df" % len(tdeltas), *tdeltas)
		data = header + body
		writefile(syncfile, data)

except InputException, e:
	print "Error in input song: %s" % e.message

