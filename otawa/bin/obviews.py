#!/usr/bin/env python3
#
# Obviews - OTAWA Binary Viewers
#
# This file is part of OTAWA
# Copyright (c) 2022, IRIT UPS.
# 
# OTAWA is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# OTAWA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with OTAWA; if not, write to the Free Software 
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

# https://www.flaticon.com/

from functools import partial
import argparse
import datetime
import glob
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser

from http import server
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


######### global state #########
VERBOSE = False
DEBUG = False
DATA_DIR = None
DOT_PATH = None
TASK = None


######### Convenient functions #########

PARAM_RE = re.compile("%([0-9a-fA-F]{2})")
def parsep(text):
	res = ""
	m = PARAM_RE.search(text)
	while m:
		res += text[:m.start()]
		res += chr(int(m[1], 16))
		text = text[m.end():]
		m = PARAM_RE.search(text)
	res += text
	return res

def error(msg):
	sys.stderr.write("ERROR: %s\n" % msg)

def warn(msg):
	sys.stderr.write("WARNING: %s\n" % msg)

def fatal(msg):
	raise FatalError(msg)

def norm(name):
	return name.replace("-", "_")


class StringBuffer():
	def __init__(self, init = ""):
		self.str = init
	
	def write(self, value):
		self.str += value
	
	def make(self):
		return self.to_utf8()

	def to_utf8(self):
		return self.str.encode("utf-8")

	def to_str(self):
		return self.str

	def to_xml(self):
		return ('<?xml version="1.0" encoding="utf8" standalone="yes"?>\n' + self.str).encode("utf-8")


class FatalError(Exception):
	"""Fatal exception in obviews."""

	def __init__(self, msg):
		self.msg = msg

	def __str__(self):
		return self.msg


def escape_dot(s):
	"""Escape a string to be compatible with dot."""
	return s. \
		replace("{", "\\{").\
		replace("}", "\\}").\
		replace("\n", "").\
		replace("\r", "")


def escape_html(s):
	"""Escape a string to be compatible with HTML text."""
	return s. \
		replace("<", "&lt;"). \
		replace(">", "&gt;"). \
		replace("&", "&amp;"). \
		replace(" ", "&nbsp;"). \
		replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")


DEF_RE = re.compile("#\s*(\S+):\s*(.*)")
class CSV:

	def __init__(self, path):
		self.path = path
		self.defs = None
		self.input = None
		self.line = None

	def open(self):
		self.input = open(self.path, encoding='utf-8')
		return self.read_line()

	def read_line(self):
		l = self.input.readline()
		if l == '':
			return None
		elif len(l) >= 1 and l[-1] == '\n':
			return l[:-1]
		else:
			return l

	def close(self):
		self.input.close()

	def read_defs(self):
		if self.defs == None:
			self.defs = {}
			l = self.open()
			while l != None:
				if not l or l[0] != "#":
					self.line = l
					break
				m = DEF_RE.match(l)
				if m:
					self.defs[m.group(1)] = m.group(2).strip(" \t\n")
				l = self.read_line()

	def read_all(self):
		if self.input == None:
			self.read_defs()
		l = self.line
		while l != None:
			yield l.split('\t')
			l = self.read_line()
		self.close()

	def consume(self, id, d):
		if id not in self.defs:
			return d
		x = self.defs[id]
		del self.defs[id]
		return x

	def all_defs(self):
		return self.defs




######## Source management #########


class SyntaxColorizer:
	
	def colorize(self, line, out):
		out.write(line)
		out.write("<br align='left'/>")

NULL_COLORIZER = SyntaxColorizer()


class CColorizer:
	
	def __init__(self):
		self.re = re.compile("(^#[a-z]+)|" +
			"(if|else|for|while|switch|case|break|continue|do|return)|" +
			"(typedef|bool|int|char|float|double|short|long|signed|unsigned|struct|union|enum)|" +
			"(//.*)|" +
			"(/\*\*+/)|" + 
			"(/\*(\*[^/]|[^\*])*\*/)|" +
			"([a-zA-Z_0-9]+)")

	def colorize(self, line, out):
		while line:
			m = self.re.search(line)
			if not m:
				out.write(line)
				break
			out.write(line[:m.start()])
			if m.group(1):
				out.write("<font color='orange'><b>%s</b></font>" % m.group(1))
			elif m.group(2):
				out.write("<font color='red'><b>%s</b></font>" % m.group(2))
			elif m.group(3):
				out.write("<b>%s</b>" % m.group(3))
			elif m.group(4) or m.group(5) or m.group(6):
				out.write("<font color='green'><i>%s</i></font>" % m.group())
			else:
				out.write(m.group())
			line = line[m.end():]
		out.write("<br align='left'/>")

SYNTAX_COLS = { ext: CColorizer \
	for ext in ['.c', '.h', '.cpp', '.hpp', '.cc', '.hh'] }


class Source:
	"""Represents a source used in the application."""

	def __init__(self, name, path):
		self.label = os.path.basename(name)
		self.name = name
		self.path = path
		self.lines = None
		self.data = []
		self.colorizer = None

	def init_lines(self):
		self.lines = list(open(self.path, "r"))

	def get_lines(self):
		if self.lines == None:
			self.init_lines()
		return self.lines

	def get_line(self, num):
		if self.lines == None:
			self.init_lines()
		if num >= len(self.lines):
			return ""
		else:
			return self.lines[num]

	def get_colorizer(self):
		if self.colorizer == None:
			try:
				self.colorizer = SYNTAX_COLS[os.path.splitext(self.path)[1]]()
			except KeyError:
				self.colorizer = NULL_COLORIZER
		return self.colorizer

	def collect(self, num, stat, val):
		if num >= len(self.data):
			self.data = self.data + [None] * (num + 1 - len(self.data))
		if self.data[num] == None:
			self.data[num] = Data()
		return self.data[num].add_val(stat, val)

	def get_stat(self, num, stat):
		if num >= len(self.data):
			return 0
		else:
			data = self.data[num]
			if data ==  None:
				return 0
			else:
				return data.get_val(stat)

	def gen(self, stat = None):
		"""Generate a source output."""
		col = self.get_colorizer()

		# generate the table
		out = StringBuffer()
		out.write('<table id="stats">\n')
		out.write(" <tr><th></th><th>source</th><th></th>\n")

		num = 0
		for l in self.get_lines():
				num = num + 1
				style = ""

				# prepare line
				if l.endswith("\n"):
					l = l[:-1]
				style = ""
				
				# compute indentation
				indent = 0
				while l:
					if l[0] == ' ':
						indent = indent + 8
					elif l[0] == '\t':
						indent = indent + 32
					else:
						break
					l = l[1:]
				if indent:
					style = style + " padding-left: %spt;" % indent

				# display the line
				out.write('<tr><td>%d</td><td class=\"source\"' % num)
				if style:
					out.write(" style=\"%s\"" % style)
				out.write(">")
				col.colorize(l, out)
				out.write("</td><td></td>")

		out.write("</tr>\n")
		out.write("</table>\n")
		r = out.to_utf8()
		return r


class SourceManager:
	"""The source manager manages sources of the processed program."""
	
	def __init__(self, task, paths = ["."]):
		self.task = task
		self.paths = paths
		self.map = {}
		self.sources = []
		self.max = Data()

	def get_sources(self):
		"""Get the sources composing the task."""
		return self.sources

	def find_actual_path(self, path):
		"""Find the actual path to the given source.
		Return None if it cannot be found."""
		if os.path.isabs(path):
			return os.path.isfile(path)
		else:
			for p in self.paths:
				p = os.path.join(p, path)
				if os.path.isfile(p):
					return p
			return None
	
	def find(self, name):
		"""Lookup for a source file. If not already loaded, lookup
		for the source using the lookup paths."""
		try:
			return self.map[name]
		except KeyError:
			source = None
			path = self.find_actual_path(name)
			if path != None:
				try:
					source = Source(name, path)
					self.sources.append(source)
				except OSError:
					pass
			self.map[name] = source
			return source

	def collect(self, path, num, stat, val):
		source = self.find(path)
		if source != None:
			x = source.collect(num, stat, val)
			self.max.max_val(stat, x)

	def get_lines(self, path):
		"""Get the lines for the given path. Return None if the path
		cannot be found."""
		if source == None:
			return None
		else:
			return source.get_lines()

	def get_line(self, path, line):
		"""Get the line corresponding to the given source path and line.
		If not found, return None."""
		source = self.find(path)
		if source == None:
			return None
		else:
			try:
				return source.lines[line - 1]
			except IndexError:
				return None

	def get_max(self,stat):
		return self.max.get_val(stat)



###### Views ######

VIEW_COLORS = [
	"red",
	"grey",
	"purple",
	"green",
	"blue",
	"chocolate",
	"coral",
	"darkcyan",
	"darkorange"
]
VIEW_COLOR = 0

class View:
	"""Represents view of the program."""

	def __init__(self, path, task):
		global VIEW_COLOR
		self.path = path
		self.task = task
		self.name = os.path.basename(path)[:-9]
		self.label = self.name
		self.description = ""
		self.data = None
		self.csv = CSV(path)
		self.csv.read_defs()
		self.label = self.csv.consume("Label", self.label)
		self.description = self.csv.consume("Description", "")
		self.defs = self.csv.all_defs()
		self.id = len(task.views)
		self.level = self.id
		task.views.append(self)
		self.color = VIEW_COLORS[VIEW_COLOR]
		VIEW_COLOR = (VIEW_COLOR + 1) % len(VIEW_COLORS)

	def priority(self):
		return 0

	def load_line(self, l):
		assert len(l) > 3
		self.data[int(l[0])][int(l[1])].append((int(l[2], 16), l[3]))

	def load_data(self):

		# prepare the data structre
		self.data = []
		for g in self.task.cfgs:
			self.data.append([[] for i in range(0, len(g.verts))])

		# load the view
		for l in self.csv.read_all():
			self.load_line(l)

	def ensure_data(self):
		if self.data == None:
			self.load_data()

	def get(self, g, v):
		"""Get the code corresponding to CFG g and vertex v.
		The result is an ordered list of pairs (instruction address,
		corresponding code)."""
		if self.data == None:
			self.load_data()
		return self.data[g.id][v.id]

	def prepare(self, out):
		"""Called just befoe generting the body of a BB."""
		pass

	def gen(self, addr, code, out):
		"""Output the code."""
		if addr != None:
			out.write("<font color=\"%s\" point-size=\"8\">&nbsp;&nbsp;%08x&nbsp;" % (self.color, addr))
		out.write(escape_html(code))
		out.write("</font><br align='left'/>")


class DisassemblyView(View):
	"""View for disassembly."""

	def __init__(self, path, task):
			View.__init__(self, path, task)

	def priority(self):
		return 1

	def gen(self, addr, code, out):
		if addr != None:
			out.write("%08x&nbsp;" % addr)
		out.write(escape_html(code))
		out.write("<br align='left'/>")


class SourceView(View):
	"""Special case of source view."""

	def __init__(self, path, task):
		View.__init__(self, path, task)
		task.sview = self
		self.level = -1

	def priority(self):
		return 2

	def load_line(self, l):
		file, line = l[3].split(":")
		self.data[int(l[0])][int(l[1])].append((int(l[2], 16), (file, int(line))))
		self.task.sman.find(file)

	def get_sources(self):
		return self.sources

	def prepare(self, out):
		self.file = None
		self.line = None

	def gen(self, addr, code, out):
		file, line = code
		source = self.task.find_source(file)
		if source == None or self.file != file or self.line + 1 != line: 
			out.write("<b><font color='blue'>%s:%d:</font></b><br align='left'/>" \
				% (escape_html(file), line))
		self.file = file
		self.line = line
		if source != None:
			t = escape_html(source.get_lines()[line-1])
			if len(t) > 0 and t[-1] == "\n":
				t = t[:-1]
			source.get_colorizer().colorize(t, out)


SPECIAL_VIEWS = {
	"source":	SourceView,
	"disassembly": DisassemblyView
}


######## Lookup definitions #########

class RGB:
	
	def __init__(self, r, g, b):
		self.val = "#%02x%02x%02x" % (r, g,b)

	def __str__(self):
		return self.val

WHITE = RGB(255, 255, 255)
BLACK = RGB(0, 0, 0)

COLORS = [
	RGB(234,231,255),
	RGB(214,207,255),
	RGB(192,183,255),
	RGB(171,158,255),
	RGB(161,148,250),
	RGB(155,142,245),
	RGB(140,125,237),
	RGB(123,108,227),
	RGB(113,98,221)
]
COLOR_TH = 4

def background(ratio):
	return COLORS[round(ratio * (len(COLORS) - 1))]

def foreground(ratio):
	if ratio * (len(COLORS) - 1) < COLOR_TH:
		return BLACK
	else:
		return WHITE


class Decorator:
	
	def __init__(self, task):
		self.sman = task.get_source_manager()
	
	def start_cfg(self, cfg):
		pass

	def end_cfg(self, cfg):
		pass

	def cfg_label(self, cfg, out):
		pass

	def bb_body(self, bb, out):
		pass

	def bb_label(self, b, out):
		pass


class StatDecorator(Decorator):
	
	def __init__(self, task):
		Decorator.__init__(self, task)
		self.task = task
	
	def bb_body(self, bb, out):
		for stat in self.task.stats:
			val = bb.get_val(stat)
			percent = val * 100. / self.task.sum.get_val(stat)
			out.write("%s=%d (%3.2f%%)<br/>" % (stat.label, val, percent))


class ViewDecorator(Decorator):

	def __init__(self, views):
		self.views = views

	def start_cfg(self, cfg):
		self.cfg = cfg

	def bb_body(self, v, out):
		g = self.cfg
		l = []
		for view in self.views:
			view.prepare(out)
		for view in self.views:
			c = view.get(g, v)
			for i in range(0, len(c)):
				l.append((c[i][0], view.level, i, view, c[i][1]))
		for (a, v, i, v, c) in sorted(l):
			v.gen(a, c, out)
			#out.write("<br align='left'/>")


class SeqDecorator(Decorator):
	"""Decorator composing sequence of decorators."""

	def __init__(self, decs):
		self.decs = decs

	def start_cfg(self, cfg):
		for dec in self.decs:
			dec.start_cfg(cfg)

	def end_cfg(self, cfg):
		for dec in self.decs:
			dec.end_cfg(cfg)

	def cfg_label(self, cfg, out):
		for dec in self.decs:
			dec.cfg_label(cfg, out)

	def bb_body(self, bb, out):
		if bb.type == BLOCK_CODE and self.decs != []:
			self.decs[0].bb_body(bb, out)
			for dec in self.decs[1:]:
				bb.gen_sep(dec, out)
				dec.bb_body(bb, out)


	
######## Program Descriptions ########

BLOCK_ENTRY = 0
BLOCK_EXIT = 1
BLOCK_CODE = 2
BLOCK_CALL = 3
BLOCK_UNKNOWN = 4
BLOCK_VIRTUAL = 5

class Data:
	
	def __init__(self):
		self.data = { }
	
	def set_val(self, id, val):
		self.data[id] = val
	
	def get_val(self, id):
		try:
			r = self.data[id]
			return r
		except KeyError:
			return 0
	
	def max_val(self, id, val):
		if val == 0:
			return 0
		try:
			self.data[id] = max(self.data[id], val)
		except KeyError:
			self.data[id] = val
		return self.data[id]

	def add_val(self, id, val):
		if val == 0:
			return 0
		try:
			self.data[id] = self.data[id] + val
		except KeyError:
			self.data[id] = val
		return self.data[id]


BLOCK_LABEL_MAP = {
	BLOCK_ENTRY:	"entry",
	BLOCK_EXIT:		"exit",
	BLOCK_UNKNOWN:	"unknown",
	BLOCK_VIRTUAL:	"virtual"
}

class Block(Data):
	
	def __init__(self, type, id):
		Data.__init__(self)
		self.type = type
		self.id = id
		self.next = []
		self.pred = []
		self.cfg = None
	
	def collect(self, id, val, addr, size, task):
		return 0

	def gen(self, dec, out):
		"""Called to generate DOT file."""
		out.write("label=\"%s\"" % BLOCK_LABEL_MAP[self.type])

	def gen_sep(self, dec, out):
		"""Called by the decorator to generate separator
		with different types of information."""
		pass
	

class BasicBlock(Block):
	
	def __init__(self, id, base, size):
		Block.__init__(self, BLOCK_CODE, id)
		self.base = base
		self.size = size

	def collect(self, id, val, addr, size, task):
		if self.base <= addr and addr < self.base + self.size:
			self.add_val(id, val)
			if task.sview != None:
				for (_, (f, l)) in task.sview.get(self.cfg, self):
					task.sman.collect(f, l, id, val)

	def gen(self, dec, out):
		num = self.id
		out.write(
			"margin=0,shape=\"box\",label=<<table border='0' cellpadding='8px'><tr><td>BB %s (%x:%s)</td></tr><hr/><tr><td align='left'>" \
			% (num, self.base, self.size))
		dec.bb_body(self, out)
		out.write("</td></tr></table>>")

	def gen_sep(self, dec, out):
		out.write("</td></tr><hr/><tr><td>")


class CallBlock(Block):

	def __init__(self, id, callee):
		Block.__init__(self, BLOCK_CALL, id)
		self.callee = callee

	def gen(self, dec, out):
		if self.callee != None:
			out.write("URL=\"javascript:call_function(%d, '%s')\",label=\"call %s\",shape=\"box\"" \
				% (self.callee.id, self.callee.label, self.callee.label))
		else:
			out.write("label=\"call unknown\",shape=\"box\"")
	

class Edge(Data):
	
	def __init__(self, src, snk, type):
		Data.__init__(self)
		self.src = src
		src.next.append(self)
		self.snk = snk
		snk.prev = self
		self.type = type

	def get_type_label(self):
		"""Get label for the edge type. Return None for not-taken."""
		if self.type == "T":
			return "taken"
		elif self.type == "N":
			return None
		else:
			return self.type


class CFG:
	
	def __init__(self, id, label, addr, ctx):
		self.id = id
		self.label = label
		self.addr = addr
		self.ctx = ctx
		self.verts = []
		self.entry = None
		self.exit = None
		self.unknown = None
		self.max = Data()
		self.sum = Data()
	
	def add(self, block):
		self.verts.append(block)
		block.cfg = self

	def find_bb(self, addr):
		"""Find the BB containing the address."""
		for v in self.verts:
			if v.type == BLOCK_CODE:
				if v.base <= addr and addr < v.base + v.size:
					return v
		return None

	def begin_stat(self, id):
		self.max.set_val(id, 0)
		self.sum.set_val(id, 0)

	def collect(self, id, val, addr, size, ctx, task):
		if ctx == self.ctx:
			for b in self.verts:
				b.collect(id, val, addr, size, task)

	def end_stat(self, id):
		for v in self.verts:
			x = v.get_val(id)
			self.max.max_val(id, x)
			self.sum.add_val(id, x)

	def gen(self, dec, out):
		"""Generate the DOT code for the CFG with the given decorator."""
		dec.start_cfg(self)
		out.write("digraph %s {\n" % self.id)
		out.write('node [ fontname = "Helvetica" ]\n')
		for b in self.verts:
			out.write("\t%s [" % b.id)
			b.gen(dec, out)
			out.write("];\n")
		for b in self.verts:
			for e in b.next:
				out.write("\t%s -> %s" % (e.src.id, e.snk.id))
				l = e.get_type_label()
				if l != None:
					out.write(" [label=\"%s\"]" % l)
				out.write(";\n");
		dec.cfg_label(self, out)
		out.write("\n}\n")
		dec.end_cfg(self)
		

class Task:
	"""Represents a task of the application."""
	
	def __init__(self, exec, name, path):
		self.exec = exec
		self.name = name
		self.path = path
		self.entry = None
		self.cfgs = []
		self.max = Data()
		self.sum = Data()
		self.stats = []
		self.sman = SourceManager([os.path.dirname(exec)])
		self.read()
		self.defs = None
		self.views = []

	def get_max(self, stat):
		return self.max.get_val(stat)

	def get_sum(self, stat):
		return self.sum.get_val(stat)

	def get_source_manager(self):
		return self.sman

	def get_sources(self):
		return self.sman.get_sources()

	def find_source(self, name):
		return self.sman.find(name)

	def find_cfg(self, addr):
		"""Find a CFG by its address."""
		for g in self.cfgs:
			if g.addr == addr:
				return g
		return None
	
	def add(self, cfg):
		"""Add a a CFG to the task."""
		if self.entry == None:
			self.entry = cfg
		self.cfgs.append(cfg)

	def begin_stat(self, id):
		self.max.set_val(id, 0)
		self.sum.set_val(id, 0)
		for g in self.cfgs:
			g.begin_stat(id)

	def collect(self, id, val, addr, size, ctx):
		"""Collect statistic item in the CFG."""
		for g in self.cfgs:
			g.collect(id, val, addr, size, ctx, self)

	def end_stat(self, id):
		for g in self.cfgs:
			g.end_stat(id)
			self.max.max_val(id, g.max.get_val(id))
			self.sum.add_val(id, g.sum.get_val(id))

	def make_cfg(self, l):
		g = CFG(len(self.cfgs), l[1], int(l[2], 16), l[3])
		self.cfgs.append(g)

	def make_entry(self, l):
		g = self.cfgs[-1]
		b = Block(BLOCK_ENTRY, len(g.verts))
		g.entry = b
		g.add(b)

	def make_exit(self, l):
		g = self.cfgs[-1]
		b = Block(BLOCK_EXIT, len(g.verts))
		g.exit = b
		g.add(b)

	def make_unknown(self, l):
		g = self.cfgs[-1]
		b = Block(BLOCK_UNKNOWN, len(g.verts))
		g.unknown = b
		g.add(b)

	def make_virtual(self, l):
		g = self.cfgs[-1]
		b = Block(BLOCK_VIRTUAL, len(g.verts))
		g.add(b)		

	def make_bb(self, l):
		g = self.cfgs[-1]
		b = BasicBlock(
			len(g.verts),
			int(l[1], 16),
			int(l[2]))
		g.add(b)

	def make_call(self, l):
		g = self.cfgs[-1]
		if len(l) < 2:
			callee = None
		else:
			callee = int(l[1])
		b = CallBlock(len(g.verts), callee)
		g.add(b)

	def make_edge(self, l):
		g = self.cfgs[-1]
		Edge(g.verts[int(l[1])], g.verts[int(l[2])], l[3])
	
	def read(self):
		"""Read the task from the file."""
		path = os.path.join(self.path, "cfg.csv")
		if not os.path.exists(path):
			fatal("no CFG file. Did you forget -W option in objdump/owcet?")
		map = {
			'G': self.make_cfg,
			'N': self.make_entry,
			'X': self.make_exit,
			'B': self.make_bb,
			'C': self.make_call,
			'E': self.make_edge,
			'U': self.make_unknown,
			'P': self.make_virtual
		}
		try:

			# parse definitions
			csv = CSV(path)
			for l in csv.read_all():
				map[l[0]](l)

			# fix call blocks
			for g in self.cfgs:
				for v in g.verts:
					if v.type == BLOCK_CALL:
						if v.callee != None:
							v.callee = self.cfgs[v.callee]

			# record defs
			self.label = csv.consume("Label", self.name)
			self.exec = csv.consume("Exec", None)
			self.defs = csv.all_defs()
			
		except OSError as e:
			raise IOError("error in reading %s: %s" % (path, e))


########## Statistics ########

OP_MAX = lambda d, s, x: d.max_val(s, x)
OP_SUM = lambda d, s, x: d.add_val(s, x)
OP_MAP = {
	"sum":	OP_SUM,
	"max":	OP_MAX
}

class Statistic:
	"""Record information about statistics."""
	name = None
	map = None
	max = None

	def __init__(self, task, name, path):
		self.task = task
		self.name = name
		self.path = path
		self.label = name
		self.line_op = OP_SUM
		self.concat_op = OP_SUM
		self.context_op = OP_MAX
		self.unit = None
		self.number = len(task.stats)
		self.total = 0
		self.description = ""
		task.stats.append(self)
		self.csv = None
		self.defs = None
		self.loaded = False

	def get_op(self, id, d):
		op = self.csv.consume(id, None)
		if op == None:
			return d
		else:
			try:
				return OP_MAP[op]
			except KeyError:
				warn("unknown line-op operator (%s) for %s"
					% (self.defs["LineOperation"], self.name))
				return d

	def preload(self):
		"""Load definitions from the statistics."""
		self.csv = CSV(self.path)
		self.csv.read_defs()
		self.label = self.csv.consume("Label", self.name)
		self.unit = self.csv.consume("Unit", self.unit)
		self.total = self.csv.consume("Total", self.unit)
		self.description = self.csv.consume("Description", self.unit)
		self.line_op = self.get_op("LineOp", OP_SUM)
		self.concat_op = self.get_op("ConcatOp", OP_SUM)
		self.context_op = self.get_op("ContextOp", OP_SUM)
		self.defs = self.csv.all_defs()

	def ensure_preload(self):
		if self.defs == None:
			self.preload()

	def load(self):
		"""Load statistics data from the file."""
		try:
			self.task.begin_stat(self)
			for fs in self.csv.read_all():
				assert len(fs) == 4
				self.task.collect(self,
					int(fs[0]),
					int(fs[1], 16),
					int(fs[2]),
					fs[3])
			self.task.end_stat(self)
		except OSError as e:
			fatal("cannot open statistics %s: %s." % (self.name, e))

	def ensure_load(self):
		"""Ensure that statistics data has been loaded."""
		self.ensure_preload()
		if not self.loaded:
			self.load()
			self.loaded = True

	def record_source(self, source):
		"""Record data in the given source."""
		self.ensure_load()
		for g in self.task.cfgs:
			for b in g.verts:
				if b.type == BLOCK_CODE:
					for (f, l) in b.lines:
						if f == source.name:
							if source.data[l-1] == None:
								source.data[l-1] = Data()
							source.data[l-1].add_val(self, b.get_val(self))

	def get_max(self):
		self.ensure_load()
		return self.task.max.get_val(self)

	def get_sum(self):
		self.ensure_load()
		return self.task.sum.get_val(self)


######### Template preprocessing #########

EXPAND_VAR = re.compile("([^\$]*)\$\{([^\}]*)\}(.*)")

def preprocess(path, map):
	"""Preprocess the given path containing string of the form ${ID}
	and getting the ID from the map. Return the preprocessed file
	as a string."""
	out = StringBuffer()
	for l in open(path, "r"):
		while l:
			m = EXPAND_VAR.search(l)
			if not m:
				out.write(l)
				break
			else:
				#print(l, "\n", m.group(1), m.group(2), m.group(3))
				r = map[m.group(2)]()
				out.write(m.group(1))
				out.write(r)
				l = m.group(3)
	return out.to_utf8()

		
def get_functions():
	"""Generate HTML to access functions."""
	out = StringBuffer()
	n = 0
	fns = []
	for f in TASK.cfgs:
		fns.append((f.label, n))
		n = n + 1
	fns.sort()
	for (l, n) in fns:
		out.write(
			'<div><a href="javascript:open_function(%s, \'%s\');">%s</a></div>' \
			% (n, l, l)
		)
		n = n + 1
	return out.to_str()


def get_sources():
	"""Generate HTML to access the sources of the current task."""
	out = StringBuffer()
	srcs = list(TASK.get_sources())
	srcs.sort(key = lambda s: s.name)
	for src in srcs:
		out.write("""<div><a href="javascript:show_source('%s')">%s</a></div>""" \
			% (src.name, src.name))
	return out.to_str()


def get_stats():
	for s in TASK.stats:
		s.ensure_preload()
	out = StringBuffer()
	out.write('<option selected>No stat.</option>')
	for s in TASK.stats:
		out.write("<option>%s</option>" % s.label)
	return out.to_str()


def get_stat_colors():
	out = StringBuffer()
	out.write("var COLORS = new Array(")
	out.write('"%s"' % str(COLORS[0]))
	for i in range(1, len(COLORS)):
		out.write(', "%s"' % COLORS[i])
	out.write(");\n")
	return out.to_str();

def get_views():
	out = StringBuffer()
	for i in range(0, len(TASK.views)):
		view = TASK.views[i]
		out.write('<input name="view%d" %stype="checkbox" onchange="javascript:view_change(this, %d);"/><label for="view%d">%s</label><br/>\n'
			% (
				i,
				"checked " if view == TASK.sview else "",
				i,
				i,
				view.label
			))
	return out.to_str()


def get_view_mask():
	for i in range(0, len(TASK.views)):
		view = TASK.views[i]
		if view == TASK.sview:
			return str(1 << i);
	return "0";
	

INDEX_MAP = {
	"functions":	get_functions,
	"sources":		get_sources,
	"stats":		get_stats,
	"stat-colors":	get_stat_colors,
	"application":	lambda: os.path.basename(os.path.splitext(TASK.exec)[0]),
	"task":			lambda: TASK.name,
	"views":		get_views,
	"view-mask":	get_view_mask
}


######### Server management #########

def do_stop(comps, query = {}):
	"""Stop the application."""
	return 666, {}, "".encode('utf-8')


def do_source(comps, query = {}):
	path = "/".join(comps)
	try:
		stat = query["stat"]
	except KeyError:
		stat = None
	source = TASK.find_source(path)
	if source == None:
		return 500, {"content-Type": "text/plain"}, b"source not available"
	else:
		return \
			200, \
			{'Content-type':"text/html; charset=utf-8"}, \
			source.gen(stat)


def do_source_stat(comps, query):
	stat = TASK.stats[int(query["stat"]) - 1]
	stat.ensure_load()
	path = parsep(query["id"])
	source = TASK.find_source(path)
	assert source != None
	out = StringBuffer();
	out.write("0 %d" % TASK.get_source_manager().get_max(stat))
	for i in range(0, len(source.get_lines())):
		x = source.get_stat(i, stat)
		if x != 0:
			out.write(" %d %d" % (i, x))
	return 200, {"content-Type": "text/plain"}, out.make()


def do_function(comps, query):
	g = TASK.cfgs[int(comps[0])]

	# define statistics decorator
	for s in TASK.stats:
		s.ensure_load()
	sdec = StatDecorator(TASK)

	# decorate with source
	#vdec = ViewDecorator([TASK.sview])
	vmask = int(query['vmask'])
	views = []
	for i in range(0, len(TASK.views)):
		if (vmask & (1 << i)) != 0:
			views.append(TASK.views[i])
	vdec = ViewDecorator(views)

	# put all together
	dec = SeqDecorator([vdec, sdec])

	# generate the dot
	(handle, path)  = tempfile.mkstemp(suffix=".dot", text=True)
	out = os.fdopen(handle, "w")
	g.gen(dec, out)
	out.close()

	# generate the SVG
	r = subprocess.run([DOT_PATH, path, "-Tsvg"], capture_output = True)
	if r.returncode != 0:
		print("ERROR: faulty .dot file:", path)
		return (
			200,
			{},
			StringBuffer("<p>Cannot generate the CFG: %s</p>" % r.returncode).to_xml()
		)

	# send the SVG
	os.remove(path)
	return 200, {}, r.stdout


def do_function_stat(comps, query):
	stat = TASK.stats[int(query["stat"]) - 1]
	stat.ensure_load()
	g = TASK.cfgs[int(query["id"])]
	out = StringBuffer()
	out.write(str(TASK.get_max(stat)))
	for v in g.verts:
		if v.type == BLOCK_CALL:
			x = v.callee.max.get_val(stat)
		else:
			x = v.get_val(stat)
		if x != 0:
			out.write(" %d %d" % (v.id, x))
	return 200, {"content-Type": "text/plain"}, out.to_utf8()


def do_stat_info(comps, query):
	stat = TASK.stats[int(query["stat"]) - 1]
	out = StringBuffer()
	out.write("<div>")
	for (k, v) in stat.defs.items():
		out.write("<b>%s:</b> %s<br/>" % (k, v))
	out.write("</div>")
	return 200, {}, out.to_xml()


def do_context(comps, query):
	out = StringBuffer()
	g = TASK.cfgs[int(query["id"])]
	cg = TASK.cfgs[0]
	fst = True
	for s in g.ctx[1:-1].split(','):
		s = s.strip()
		if s == "":
			break;
		if s.startswith("FUN("):
			cg = TASK.find_cfg(int(s[4:-1], 16))
			if cg != None:
				s = """<a href="javascript: open_function(%d, '%s');">%s</a>""" \
					% (cg.id, cg.label, cg.label)
		elif s.startswith("CALL("):
			if cg != None:
				bb = cg.find_bb(int(s[5:-1], 16))
				if bb != None and TASK.sview != None:
					l = TASK.sview.get(cg, bb)
					if l != []:
						file = l[-1][1][0]
						line = l[-1][1][1]
						s = """<a href="javascript: show_source('%s');">%s:%d</a>""" \
							% (file, file, line)
		out.write(s)
		out.write('<img src="ctxsep.png" style="width: 1em;"/>')
	out.write(g.label)			
	return 200, {"content-Type": "text/plain"}, out.to_utf8()
	
	
DO_MAP = {
	"stop": 			do_stop,
	"source":			do_source,
	"source-stat":		do_source_stat,
	"function":			do_function,
	"function-stat":	do_function_stat,
	"stat-info":		do_stat_info,
	"context":			do_context
}



class Handler(BaseHTTPRequestHandler):
	"""Handle HTTP requests."""

	def route(self, path='', query={}):
		"""Process a request and return the anwer."""

		comps = path.split('/', 2)
		try:
			return DO_MAP[comps[1]](comps[2:], query)
		except KeyError:
			if comps[1] == "":
				comps[1] = "index.html"
			path = os.path.join(DATA_DIR, "/".join(comps[1:]))
			if comps[1] == "index.html":
				return 200, \
					{}, \
					preprocess(path, INDEX_MAP)
			else:
				r = mimetypes.guess_type(path)
				try:
					return 200, \
						{"Content-Type": r[0]}, \
						open(path, 'rb').read()
				except FileNotFoundError:
					return 404, None, ""

	def do_GET(self):
	
		# parse URL
		urlP = urllib.parse.urlparse(self.path)
		query = {t[0] : t[1] for t in [p.split('=') if '=' in p else [p,''] for p in urlP.query.split('&')]}

		# manage the request
		quit = False
		if not DEBUG:
			response_code , headers, data = self.route(urlP.path, query)
		else:
			try:
				response_code , headers, data = self.route(urlP.path, query)
			except Exception as err:
				print(err)
				response_code = 500
				headers = {}
				data = str(err).encode('utf-8')
		if response_code == 666:
			quit = True
			response_code = 204

		# build the answer
		if type(response_code) is tuple:
			code = response_code[0]
			message = response_code[1]
		else:
			code = response_code
			message = None
		self.send_response(code, message)
		for key in headers:
			self.send_header(key, headers[key])
		self.send_header('Access-Control-Allow-Origin', '*')
		self.end_headers()
		self.wfile.write(data)
		if quit:
			sys.exit(0)

	do_POST = do_GET

	def log_error(self, fmt, *args):
		http.server.BaseHTTPRequestHandler.log_message(self, fmt % args)

	def log_message(self, fmt, *args):
		pass


######### Start-up #########

BROWSERS = [
	("chromium", "chromium --app=%s --new-window"),
	("google-chrome", "chromium --app=%s --new-window")
]

def open_browser(port, debug = False):
	"""Open the connection into a browser."""
	time.sleep(.5)
	addr = "http://localhost:%d" % port

	# debug mode
	if debug:
		webbrowser.open(addr)
		return

	# look for available browser
	for (browser, cmd) in BROWSERS:
		if shutil.which(browser) != None:
			res = subprocess.run(cmd % addr, shell=True)
			if res.returncode == 0:
				return

	# use the default browser
	webbrowser.open("http://localhost:%d" % port, new=1)


def main():
	global APPLICATION
	global DATA_DIR
	global DOT_PATH
	global SOURCE_MANAGER
	global STATS
	global TASK
	global DEBUG
	global PORT

	# check for dot
	DOT_PATH = shutil.which("dot")
	if DOT_PATH == None:
		fatal("dot command (from GraphViz package) must be available!")

	# parse arguments
	parser = argparse.ArgumentParser(description = "WCET viewer for OTAWA")
	parser.add_argument('executable', type=str,
		help="Select the exacutable to get statistics from.")
	parser.add_argument('task', nargs="?", type=str,
		help="Select which task to display (default main function).")
	parser.add_argument("--debug", action="store_true",
		help="Enable debugging mode.")
	parser.add_argument("--serve", action="store_true",
		help="Work as a server.")
	parser.add_argument("--port", type=int, default='0',
		help="Specify which port to use.")
	args = parser.parse_args()
	if args.debug:
		DEBUG = True
		print("INFO: debug mode enabled.")
	serve = False
	if args.serve:
		serve = True
		print("INFO: server mode enabled.")
	PORT = args.port

	# find resources
	otawa_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	DATA_DIR = None
	for path in [
		os.path.join(otawa_path, "data/obviews"),
		os.path.join(otawa_path, "share/Otawa/obviews")
	]:
		if os.path.exists(path):
			DATA_DIR = path
			break
	if DATA_DIR == None:
		fatal("cannot find internal data!\n")

	# load task information
	exe_dir = os.path.dirname(os.path.splitext(args.executable)[0])
	exe_name = os.path.basename(os.path.splitext(args.executable)[0])
	if not args.task:
		task_name = "main"
	else:
		task_name = args.task
	task_dir = os.path.join(exe_dir, exe_name + "-otawa", task_name )
	if not os.path.exists(task_dir):
		fatal("no statistics for %s task %s. Did you forget --stats option in owcet?" % (args.executable, task_name))
	TASK = Task(args.executable, task_name, task_dir)

	# load views
	for s in glob.glob(os.path.join(task_dir, "*-view.csv")):
		try:
			cls = SPECIAL_VIEWS[os.path.basename(s)[:-9]]
		except KeyError:
			cls = View
		view = cls(s, TASK)
	if TASK.sview != None:
		TASK.sview.ensure_data()
	TASK.views.sort(key = lambda v: v.priority(), reverse=True)
	for i in range(0, len(TASK.views)):
		TASK.views[i].level = i

	# load statistics
	for s in glob.glob(os.path.join(task_dir, "*-stat.csv")):
		stat = Statistic(TASK, os.path.basename(s)[:-4], s)
		stat.ensure_load()

	# start browser and server
	with HTTPServer(("localhost", PORT), Handler) as server:
		port = server.server_address[1]
		if DEBUG or serve:
			print("INFO: listening to http://localhost:%d" % port)
		if not serve:
			Thread(target=partial(open_browser, port, DEBUG)).start()
		server.serve_forever()

if __name__ == "__main__":
	try:
		main()
	except KeyboardInterrupt:
		exit(0)
	except FatalError as e:
		sys.stderr.write("ERROR: %s\n" % e)
