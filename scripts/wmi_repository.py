#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Script to parse WMI Common Information Model (CIM) repository files."""

from __future__ import print_function
import argparse
import datetime
import glob
import logging
import os
import sys

import construct

import hexdump


# pylint: disable=logging-format-interpolation

def FromFiletime(filetime):
  """Converts a FILETIME timestamp into a Python datetime object.

    The FILETIME is mainly used in Windows file formats and NTFS.

    The FILETIME is a 64-bit value containing:
      100th nano seconds since 1601-01-01 00:00:00

    Technically FILETIME consists of 2 x 32-bit parts and is presumed
    to be unsigned.

    Args:
      filetime: The 64-bit FILETIME timestamp.

  Returns:
    A datetime object containing the date and time or None.
  """
  if filetime < 0:
    return None
  timestamp, _ = divmod(filetime, 10)

  return datetime.datetime(1601, 1, 1) + datetime.timedelta(
      microseconds=timestamp)


class IndexBinaryTreePage(object):
  """Class that contains an index binary-tree page.

  Attributes:
    keys: a list of strings containing index binary-tree keys.
    page_type: an integer containing the page type.
    root_page_number: an integer containing the root page number.
    sub_pages: a list of integers containing the sub page numbers.
  """

  PAGE_SIZE = 8192

  _KEY_SEGMENT_SEPARATOR = u'\\'

  _PAGE_HEADER = construct.Struct(
      u'page_header',
      construct.ULInt32(u'page_type'),
      construct.ULInt32(u'mapped_page_number'),
      construct.ULInt32(u'unknown2'),
      construct.ULInt32(u'root_page_number'),
      construct.ULInt32(u'number_of_keys'))

  _PAGE_KEY_NUMBER_OF_SEGMENTS = construct.ULInt16(u'number_of_segments')

  _PAGE_TYPES = {
      0xaccc: u'Is active',
      0xaddd: u'Is administrative',
      0xbadd: u'Is deleted',
  }

  def __init__(self, debug=False):
    """Initializes the index binary-tree page object.

    Args:
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(IndexBinaryTreePage, self).__init__()
    self._debug = debug
    self._key_offsets = None
    self._number_of_keys = None
    self._page_key_segments = []
    self._page_values = []
    self._page_value_offsets = None

    self.keys = []
    self.page_type = None
    self.root_page_number = None
    self.sub_pages = []

  def _ReadHeader(self, file_object):
    """Reads a page header.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the page header cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading page header at offset: 0x{0:08x}'.format(file_offset))

    page_header_data = file_object.read(self._PAGE_HEADER.sizeof())

    try:
      page_header_struct = self._PAGE_HEADER.parse(page_header_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse page header at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    self.page_type = page_header_struct.get(u'page_type')
    self.root_page_number = page_header_struct.get(u'root_page_number')
    self._number_of_keys = page_header_struct.get(u'number_of_keys')

    if self._debug:
      print(u'Page header data:')
      print(hexdump.Hexdump(page_header_data))

    if self._debug:
      print(u'Page type\t\t\t\t\t\t\t\t: 0x{0:04x} ({1:s})'.format(
          self.page_type, self._PAGE_TYPES.get(self.page_type, u'Unknown')))
      print(u'Mapped page number\t\t\t\t\t\t\t: {0:d}'.format(
          page_header_struct.get(u'mapped_page_number')))
      print(u'Unknown2\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(
          page_header_struct.get(u'unknown2')))
      print(u'Root page number\t\t\t\t\t\t\t: {0:d}'.format(
          self.root_page_number))
      print(u'Number of keys\t\t\t\t\t\t\t\t: {0:d}'.format(
          self._number_of_keys))
      print(u'')

  def _ReadKeyOffsets(self, file_object):
    """Reads page key offsets.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the page key offsets cannot be read.
    """
    if self._number_of_keys == 0:
      return

    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading page keys offsets at offset: 0x{0:08x}'.format(
          file_offset))

    offsets_data_size = self._number_of_keys * 2
    offsets_data = file_object.read(offsets_data_size)

    if self._debug:
      print(u'Page keys offsets data:')
      print(hexdump.Hexdump(offsets_data))

    try:
      self._key_offsets = construct.Array(
          self._number_of_keys, construct.ULInt16(u'offset')).parse(
              offsets_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse page keys offsets at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      for index in range(self._number_of_keys):
        print(u'Page key: {0:d} offset\t\t\t\t\t\t\t: 0x{1:04x}'.format(
            index, self._key_offsets[index]))
      print(u'')

  def _ReadKeyData(self, file_object):
    """Reads page key data.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the page key data cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading page keys data at offset: 0x{0:08x}'.format(file_offset))

    try:
      data_size = construct.ULInt16(u'data_size').parse_stream(file_object)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse page keys data size at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if data_size == 0:
      return

    data_size *= 2

    if self._debug:
      print(u'Page keys data size\t\t\t\t\t\t\t: {0:d} bytes'.format(
          data_size))

    data = file_object.read(data_size)

    if self._debug:
      print(u'Page keys data:')
      print(hexdump.Hexdump(data))

    for index in range(len(self._key_offsets)):
      page_key_offset = self._key_offsets[index] * 2
      page_key_size = page_key_offset + 2

      try:
        number_of_segments = self._PAGE_KEY_NUMBER_OF_SEGMENTS.parse(
            data[page_key_offset:page_key_size])
      except construct.FieldError as exception:
        raise IOError((
            u'Unable to parse page key: {0:d} data size '
            u'with error: {1:s}').format(index, exception))

      page_key_size = page_key_offset + (number_of_segments * 2) + 2

      if self._debug:
        print(u'Page key: {0:d} data:'.format(index))
        print(hexdump.Hexdump(data[page_key_offset:page_key_size]))

      page_key_offset += 2

      try:
        page_key_segments = construct.Array(
            number_of_segments, construct.ULInt16(u'segment_index')).parse(
                data[page_key_offset:page_key_size])
      except construct.FieldError as exception:
        raise IOError((
            u'Unable to parse page key: {0:d} segments '
            u'with error: {1:s}').format(index, exception))

      self._page_key_segments.append(page_key_segments)

      if self._debug:
        print(
            u'Page key: {0:d} number of segments\t\t\t\t\t\t: {1:d}'.format(
                index, number_of_segments))
        page_key_segments_string = u', '.join([
            u'{0:d}'.format(segment_index)
            for segment_index in page_key_segments])
        print(u'Page key: {0:d} segments\t\t\t\t\t\t\t: {1:s}'.format(
            index, page_key_segments_string))
        print(u'')

  def _ReadValueOffsets(self, file_object):
    """Reads page value offsets.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the page value offsets cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading page value offsets at offset: 0x{0:08x}'.format(
          file_offset))

    try:
      number_of_offsets = construct.ULInt16(u'number_of_offsets').parse_stream(
          file_object)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse number of page value offsets at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      print(u'Number of offsets\t\t\t\t\t\t\t: {0:d}'.format(number_of_offsets))

    if number_of_offsets == 0:
      return

    offsets_data = file_object.read(number_of_offsets * 2)

    if self._debug:
      print(u'Page value offsets:')
      print(hexdump.Hexdump(offsets_data))

    try:
      offset_array = construct.Array(
          number_of_offsets, construct.ULInt16(u'offset')).parse(offsets_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse page value offsets at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      for index in range(number_of_offsets):
        print(u'Page value: {0:d} offset\t\t\t\t\t\t\t: 0x{1:04x}'.format(
            index, offset_array[index]))
      print(u'')

    self._page_value_offsets = offset_array

  def _ReadValueData(self, file_object):
    """Reads page value data.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the page value data cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading page value data at offset: 0x{0:08x}'.format(file_offset))

    try:
      data_size = construct.ULInt16(u'data_size').parse_stream(file_object)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse page value data size at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      print(u'Page value data size\t\t\t\t\t\t\t: {0:d} bytes'.format(
          data_size))

    if data_size == 0:
      return

    data = file_object.read(data_size)

    if self._debug:
      print(u'Page value data:')
      print(hexdump.Hexdump(data))

    for index in range(len(self._page_value_offsets)):
      page_value_offset = self._page_value_offsets[index]
      # TODO: determine size

      value_string = construct.CString(u'string').parse(
          data[page_value_offset:])
      if self._debug:
        print(u'Page value: {0:d} data: {1:s}'.format(index, value_string))

      self._page_values.append(value_string)

    if self._debug and self._page_value_offsets:
      print(u'')

  def _ReadSubPages(self, file_object):
    """Reads sub pages data.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the sub pages data cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading sub pages at offset: 0x{0:08x}'.format(file_offset))

    number_of_entries = self._number_of_keys + 1
    entries_data_size = number_of_entries * 4
    entries_data = file_object.read(entries_data_size)

    if self._debug:
      print(u'Sub pages array data:')
      print(hexdump.Hexdump(entries_data))

    try:
      sub_pages_array = construct.Array(
          number_of_entries, construct.ULInt32(u'page_number')).parse(
              entries_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse sub pages at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    for index in range(number_of_entries):
      page_number = sub_pages_array[index]
      if page_number not in (0, 0xffffffff):
        self.sub_pages.append(page_number)

      if self._debug:
        if page_number in (0, 0xffffffff):
          print((
              u'Sub page: {0:d} mapped page number\t\t\t\t\t\t: 0x{1:08x} '
              u'(unavailable)').format(index, page_number))
        else:
          print(u'Sub page: {0:d} mapped page number\t\t\t\t\t\t: {1:d}'.format(
              index, page_number))

      if self._debug:
        print(u'')

  def ReadPage(self, file_object, file_offset):
    """Reads a page.

    Args:
      file_object: a file-like object.
      file_offset: integer containing the offset of the page relative
                   from the start of the file.

    Raises:
      IOError: if the page cannot be read.
    """
    file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      print(u'Reading index binary-tree page at offset: 0x{0:08x}'.format(
          file_offset))

    self._ReadHeader(file_object)

    if self._number_of_keys > 0:
      array_data_size = self._number_of_keys * 4
      array_data = file_object.read(array_data_size)

      if self._debug:
        print(u'Unknown array data:')
        print(hexdump.Hexdump(array_data))

    self._ReadSubPages(file_object)
    self._ReadKeyOffsets(file_object)
    self._ReadKeyData(file_object)
    self._ReadValueOffsets(file_object)
    self._ReadValueData(file_object)

    trailing_data_size = (
        (file_offset + self.PAGE_SIZE) - file_object.tell())
    trailing_data = file_object.read(trailing_data_size)

    if self._debug:
      print(u'Trailing data:')
      print(hexdump.Hexdump(trailing_data))

    self.keys = []
    for page_key_segments in self._page_key_segments:
      key_segments = []
      for segment_index in page_key_segments:
        key_segments.append(self._page_values[segment_index])

      key_path = u'{0:s}{1:s}'.format(
          self._KEY_SEGMENT_SEPARATOR,
          self._KEY_SEGMENT_SEPARATOR.join(key_segments))

      self.keys.append(key_path)


class IndexBinaryTreeFile(object):
  """Class that contains an index binary-tree (Index.btr) file."""

  def __init__(self, index_mapping_file, debug=False):
    """Initializes the index binary-tree file object.

    Args:
      index_mapping_file: an index mapping file (instance of MappingFile).
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(IndexBinaryTreeFile, self).__init__()
    self._debug = debug
    self._file_object = None
    self._file_object_opened_in_object = False
    self._file_size = 0

    self._index_mapping_file = index_mapping_file
    self._first_mapped_page = None
    self._root_page = None

  def _GetPage(self, page_number):
    """Retrieves a specific page.

    Args:
      page_number: an integer containing the page number.

    Returns:
      An index binary-tree page (instance of IndexBinaryTreePage) or None.
    """
    file_offset = page_number * IndexBinaryTreePage.PAGE_SIZE
    if file_offset >= self._file_size:
      return

    # TODO: cache pages.
    return self._ReadPage(file_offset)

  def _ReadPage(self, file_offset):
    """Reads a page.

    Args:
      file_offset: integer containing the offset of the page relative
                   from the start of the file.

    Return:
      An index binary-tree page (instance of IndexBinaryTreePage).

    Raises:
      IOError: if the page cannot be read.
    """
    index_page = IndexBinaryTreePage(debug=self._debug)
    index_page.ReadPage(self._file_object, file_offset)
    return index_page

  def Close(self):
    """Closes the index binary-tree file."""
    if self._file_object_opened_in_object:
      self._file_object.close()
    self._file_object = None

  def GetFirstMappedPage(self):
    """Retrieves the first mapped page.

    Returns:
      An index binary-tree page (instance of IndexBinaryTreePage) or None.
    """
    if not self._first_mapped_page:
      page_number = self._index_mapping_file.mappings[0]

      index_page = self._GetPage(page_number)
      if not index_page:
        logging.warning((
            u'Unable to read first mapped index binary-tree page: '
            u'{0:d}.').format(page_number))
        return

      if index_page.page_type != 0xaddd:
        logging.warning(u'First mapped index binary-tree page type mismatch.')
        return

      self._first_mapped_page = index_page

    return self._first_mapped_page

  def GetMappedPage(self, page_number):
    """Retrieves a specific mapped page.

    Args:
      page_number: an integer containing the page number.

    Returns:
      An index binary-tree page (instance of IndexBinaryTreePage) or None.
    """
    mapped_page_number = self._index_mapping_file.mappings[page_number]

    index_page = self._GetPage(mapped_page_number)
    if not index_page:
      logging.warning(
          u'Unable to read index binary-tree mapped page: {0:d}.'.format(
              page_number))
      return

    return index_page

  def GetRootPage(self):
    """Retrieves the root page.

    Returns:
      An index binary-tree page (instance of IndexBinaryTreePage) or None.
    """
    if not self._root_page:
      first_mapped_page = self.GetFirstMappedPage()
      if not first_mapped_page:
        return

      page_number = self._index_mapping_file.mappings[
          first_mapped_page.root_page_number]

      index_page = self._GetPage(page_number)
      if not index_page:
        logging.warning(
            u'Unable to read index binary-tree root page: {0:d}.'.format(
                page_number))
        return

      self._root_page = index_page

    return self._root_page

  def Open(self, filename):
    """Opens the index binary-tree file.

    Args:
      filename: a string containing the filename.
    """
    stat_object = os.stat(filename)
    self._file_size = stat_object.st_size

    self._file_object = open(filename, 'rb')
    self._file_object_opened_in_object = True

    if self._debug:
      file_offset = 0
      while file_offset < self._file_size:
        self._ReadPage(file_offset)
        file_offset += IndexBinaryTreePage.PAGE_SIZE


class MappingFile(object):
  """Class that contains mappings (*.map) file.

  Attributes:
    data_size: an integer containing the data size of the mappings file.
    mapping: a list of integers containing the mappings to page
             numbers in the index binary-tree or objects data file.
  """

  _FOOTER_SIGNATURE = 0x0000dcba
  _HEADER_SIGNATURE = 0x0000abcd

  _FILE_FOOTER = construct.Struct(
      u'file_footer',
      construct.ULInt32(u'signature'))

  _FILE_HEADER = construct.Struct(
      u'file_header',
      construct.ULInt32(u'signature'),
      construct.ULInt32(u'format_version'),
      construct.ULInt32(u'number_of_pages'))

  def __init__(self, debug=False):
    """Initializes the mappings file object.

    Args:
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(MappingFile, self).__init__()
    self._debug = debug
    self._file_object = None
    self._file_object_opened_in_object = False
    self._file_size = 0

    self.data_size = 0
    self.mappings = []

  def _ReadFileFooter(self):
    """Reads the file footer.

    Raises:
      IOError: if the file footer cannot be read.
    """
    file_footer_data = self._file_object.read(self._FILE_FOOTER.sizeof())

    if self._debug:
      print(u'File footer data:')
      print(hexdump.Hexdump(file_footer_data))

    try:
      file_footer_struct = self._FILE_FOOTER.parse(file_footer_data)
    except construct.FieldError as exception:
      file_offset = self._file_object.tell()
      raise IOError((
          u'Unable to parse file footer at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    signature = file_footer_struct.get(u'signature')

    if self._debug:
      print(u'Signature\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(signature))

    if signature != self._FOOTER_SIGNATURE:
      raise IOError(
          u'Unsupported file footer signature: 0x{0:08x}'.format(signature))

    if self._debug:
      print(u'')

  def _ReadFileHeader(self, file_offset=0):
    """Reads the file header.

    Args:
      file_offset: optional integer containing the offset of the mappings file
                   relative from the start of the file.

    Raises:
      IOError: if the file header cannot be read.
    """
    self._file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      print(u'Reading file header at offset: 0x{0:08x}'.format(file_offset))

    file_header_data = self._file_object.read(self._FILE_HEADER.sizeof())

    if self._debug:
      print(u'File header data:')
      print(hexdump.Hexdump(file_header_data))

    try:
      file_header_struct = self._FILE_HEADER.parse(file_header_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse file header at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    signature = file_header_struct.get(u'signature')
    format_version = file_header_struct.get(u'format_version')
    number_of_pages = file_header_struct.get(u'number_of_pages')

    if self._debug:
      print(u'Signature\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(signature))
      print(u'Format version\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(format_version))
      print(u'Number of pages\t\t\t\t\t\t\t\t: {0:d}'.format(number_of_pages))

    if signature != self._HEADER_SIGNATURE:
      raise IOError(
          u'Unsupported file header signature: 0x{0:08x}'.format(signature))

    if self._debug:
      print(u'')

  def _ReadMappings(self):
    """Reads the mappings.

    Raises:
      IOError: if the mappings cannot be read.
    """
    file_offset = self._file_object.tell()
    if self._debug:
      print(u'Reading mappings at offset: 0x{0:08x}'.format(file_offset))

    try:
      number_of_entries = construct.ULInt32(u'number_of_entries').parse_stream(
          self._file_object)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse number of mapping entries at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      print(u'Number of entries\t\t\t\t\t\t\t: {0:d}'.format(number_of_entries))

    entries_data = self._file_object.read(number_of_entries * 4)

    if self._debug:
      print(u'Entries data:')
      print(hexdump.Hexdump(entries_data))

    try:
      mappings_array = construct.Array(
          number_of_entries, construct.ULInt32(u'page_number')).parse(
              entries_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse mapping entries at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      for index in range(number_of_entries):
        page_number = mappings_array[index]
        if page_number == 0xffffffff:
          print((
              u'Mapping entry: {0:d} page number\t\t\t\t\t\t: 0x{1:08x} '
              u'(unavailable)').format(index, page_number))
        else:
          print(u'Mapping entry: {0:d} page number\t\t\t\t\t\t: {1:d}'.format(
              index, page_number))
      print(u'')

    self.mappings = mappings_array

  def _ReadUnknownEntries(self):
    """Reads unknown entries.

    Raises:
      IOError: if the unknown entries cannot be read.
    """
    file_offset = self._file_object.tell()
    if self._debug:
      print(u'Reading unknown entries at offset: 0x{0:08x}'.format(file_offset))

    try:
      number_of_entries = construct.ULInt32(u'number_of_entries').parse_stream(
          self._file_object)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse number of unknown entries at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      print(u'Number of entries\t\t\t\t\t\t\t: {0:d}'.format(number_of_entries))

    entries_data = self._file_object.read(number_of_entries * 4)

    if self._debug:
      print(u'Entries data:')
      print(hexdump.Hexdump(entries_data))

    try:
      unknown_entries_array = construct.Array(
          number_of_entries, construct.ULInt32(u'page_number')).parse(
              entries_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse unknown entries at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      for index in range(number_of_entries):
        page_number = unknown_entries_array[index]
        if page_number == 0xffffffff:
          print((
              u'Unknown entry: {0:d} page number\t\t\t\t\t\t: 0x{1:08x} '
              u'(unavailable)').format(index, page_number))
        else:
          print(u'Unknown entry: {0:d} page number\t\t\t\t\t\t: {1:d}'.format(
              index, page_number))
      print(u'')

  def Close(self):
    """Closes the mappings file."""
    if self._file_object_opened_in_object:
      self._file_object.close()
    self._file_object = None

  def Open(self, filename, file_offset=0):
    """Opens the mappings file.

    Args:
      filename: a string containing the filename.
      file_offset: optional integer containing the offset of the mappings file
                   relative from the start of the file.
    """
    stat_object = os.stat(filename)
    self._file_size = stat_object.st_size

    self._file_object = open(filename, 'rb')
    self._file_object_opened_in_object = True

    self._ReadFileHeader(file_offset=file_offset)
    self._ReadMappings()
    self._ReadUnknownEntries()
    self._ReadFileFooter()

    self.data_size = self._file_object.tell() - file_offset


class ObjectDescriptor(object):
  """Class that contains an object descriptor.

  Attributes:
    data_checksum: an integer containing the checksum of the object record data.
    data_offset: an integer containing the data offset of the object record.
    data_size: an integer containing the data size of the object record.
    identifier: an integer containing the identifier of the object record.
  """

  def __init__(self, identifier, data_offset, data_size, data_checksum):
    """Initializes the object descriptor object.

    Args:
      identifier: an integer containing the identifier of the object record.
      data_offset: an integer containing the data offset of the object record.
      data_size: an integer containing the data size of the object record.
      data_checksum: an integer containing the checksum of the object record
                     data.
    """
    super(ObjectDescriptor, self).__init__()
    self.data_checksum = data_checksum
    self.data_offset = data_offset
    self.data_size = data_size
    self.identifier = identifier


class ObjectsDataPage(object):
  """Class that contains an objects data page.

  Attributes:
  """

  PAGE_SIZE = 8192

  _OBJECT_DESCRIPTOR = construct.Struct(
      u'object_descriptor',
      construct.ULInt32(u'identifier'),
      construct.ULInt32(u'data_offset'),
      construct.ULInt32(u'data_size'),
      construct.ULInt32(u'data_checksum'))

  _EMPTY_OBJECT_DESCRIPTOR = b'\x00' * _OBJECT_DESCRIPTOR.sizeof()

  def __init__(self, debug=False):
    """Initializes the objects data page object.

    Args:
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(ObjectsDataPage, self).__init__()
    self._debug = debug

    self._object_descriptors = []

  def _ReadObjectDescriptor(self, file_object):
    """Reads an object descriptor.

    Args:
      file_object: a file-like object.

    Returns:
      An object descriptor (instance of ObjectDescriptor) or None.

    Raises:
      IOError: if the object descriptor cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      print(u'Reading object descriptor at offset: 0x{0:08x}'.format(
          file_offset))

    object_descriptor_data = file_object.read(
        self._OBJECT_DESCRIPTOR.sizeof())

    if self._debug:
      print(u'Object descriptor data:')
      print(hexdump.Hexdump(object_descriptor_data))

    # The last object descriptor (terminator) is filled with 0-byte values.
    if object_descriptor_data == self._EMPTY_OBJECT_DESCRIPTOR:
      return

    try:
      object_descriptor_struct = self._OBJECT_DESCRIPTOR.parse(
          object_descriptor_data)
    except construct.FieldError as exception:
      raise IOError(
          u'Unable to parse object descriptor with error: {0:s}'.format(
              exception))

    identifier = object_descriptor_struct.get(u'identifier')
    data_offset = object_descriptor_struct.get(u'data_offset')
    data_size = object_descriptor_struct.get(u'data_size')
    data_checksum = object_descriptor_struct.get(u'data_checksum')

    if self._debug:
      print(u'Identifier\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(
          identifier))
      print(u'Data offset\t\t\t\t\t\t\t\t: 0x{0:08x} (0x{1:08x})'.format(
          data_offset, file_offset + data_offset))
      print(u'Data size\t\t\t\t\t\t\t\t: {0:d}'.format(data_size))
      print(u'Checksum\t\t\t\t\t\t\t\t: 0x{0:08x}'.format(data_checksum))
      print(u'')

    return ObjectDescriptor(identifier, data_offset, data_size, data_checksum)

  def _ReadObjectDescriptors(self, file_object):
    """Reads object descriptors.

    Args:
      file_object: a file-like object.

    Raises:
      IOError: if the object descriptor cannot be read.
    """
    file_offset = file_object.tell()
    while True:
      object_descriptor = self._ReadObjectDescriptor(file_object)
      if not object_descriptor:
        break

      # Make the offset relative to the start of the file.
      object_descriptor.data_offset += file_offset
      self._object_descriptors.append(object_descriptor)

  def ReadPage(self, file_object, file_offset):
    """Reads a page.

    Args:
      file_object: a file-like object.
      file_offset: integer containing the offset of the page relative
                   from the start of the file.

    Raises:
      IOError: if the page cannot be read.
    """
    file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      print(u'Reading objects data page at offset: 0x{0:08x}'.format(
          file_offset))

    self._ReadObjectDescriptors(file_object)

  def ReadObjectRecord(self, file_object, record_identifier, data_size):
    """Reads an object record.

    Args:
      file_object: a file-like object.
      record_identifier: an integer containing the object record identifier.
      data_size: an integer containing the object record data size.

    Returns:
      A binary string containing the object record data.

    Raises:
      IOError: if the object record cannot be read.
    """
    object_descriptor_match = None
    for object_descriptor in self._object_descriptors:
      if object_descriptor.identifier == record_identifier:
        object_descriptor_match = object_descriptor
        break

    if not object_descriptor_match:
      logging.warning(u'Object record data not found.')
      return

    if object_descriptor_match.data_size != data_size:
      logging.warning(u'Object record data size mismatch.')
      return

    file_object.seek(object_descriptor_match.data_offset, os.SEEK_SET)

    if self._debug:
      print(u'Reading object record at offset: 0x{0:08x}'.format(
          object_descriptor_match.data_offset))

    object_record_data = file_object.read(data_size)

    if self._debug:
      print(u'Object record data:')
      print(hexdump.Hexdump(object_record_data))

    return object_record_data


class ObjectsDataFile(object):
  """Class that contains an objects data (Objects.data) file."""

  _KEY_SEGMENT_SEPARATOR = u'\\'
  _KEY_VALUE_SEPARATOR = u'.'

  _KEY_VALUE_PAGE_NUMBER_INDEX = 1
  _KEY_VALUE_RECORD_IDENTIFIER_INDEX = 2
  _KEY_VALUE_DATA_SIZE_INDEX = 3

  _CLASS_DEFINITION_OBJECT_RECORD = construct.Struct(
      u'class_definition_object_record',
      construct.ULInt32(u'super_class_name_string_size'),
      construct.Bytes(
          u'super_class_name_string',
          lambda ctx: ctx.super_class_name_string_size * 2),
      construct.ULInt64(u'date_time'),
      construct.ULInt32(u'data_size'),
      construct.Bytes(u'data', lambda ctx: ctx.data_size - 4))

  _CLASS_DEFINITION_HEADER = construct.Struct(
      u'class_definition_header',
      construct.Byte(u'unknown1'),
      construct.ULInt32(u'class_name_offset'),
      construct.ULInt32(u'default_value_size'),
      construct.ULInt32(u'super_class_name_block_size'),
      construct.Bytes(
          u'super_class_name_block_data',
          lambda ctx: ctx.super_class_name_block_size - 4),
      construct.ULInt32(u'qualifiers_block_size'),
      construct.Bytes(
          u'qualifiers_block_data',
          lambda ctx: ctx.qualifiers_block_size - 4),
      construct.ULInt32(u'number_of_propery_references'))

  # TODO: add more values.

  _SUPER_CLASS_NAME_BLOCK = construct.Struct(
      u'super_class_name_block',
          construct.Byte(u'super_class_name_string_flags'),
          construct.CString(u'super_class_name_string'),
          construct.ULInt32(u'super_class_name_string_size'))

  _INTERFACE_OBJECT_RECORD = construct.Struct(
      u'interface_object_record',
      construct.Bytes(u'string_digest_hash', 64),
      construct.ULInt64(u'date_time1'),
      construct.ULInt64(u'date_time2'),
      construct.ULInt32(u'data_size'),
      construct.Bytes(u'data', lambda ctx: ctx.data_size - 4))

  _REGISTRATION_OBJECT_RECORD = construct.Struct(
      u'registration_object_record',
      construct.ULInt32(u'name_space_string_size'),
      construct.Bytes(
          u'name_space_string', lambda ctx: ctx.name_space_string_size * 2),
      construct.ULInt32(u'class_name_string_size'),
      construct.Bytes(
          u'class_name_string', lambda ctx: ctx.class_name_string_size * 2),
      construct.ULInt32(u'attribute_name_string_size'),
      construct.Bytes(
          u'attribute_name_string',
          lambda ctx: ctx.attribute_name_string_size * 2),
      construct.ULInt32(u'attribute_value_string_size'),
      construct.Bytes(
          u'attribute_value_string',
          lambda ctx: ctx.attribute_value_string_size * 2),
      construct.Bytes(u'unknown1', 8))

  def __init__(self, objects_mapping_file, debug=False):
    """Initializes the objects data file object.

    Args:
      objects_mapping_file: an objects mapping file (instance of MappingFile).
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(ObjectsDataFile, self).__init__()
    self._debug = debug
    self._file_object = None
    self._file_object_opened_in_object = False
    self._file_size = 0

    self._objects_mapping_file = objects_mapping_file

  def _GetPage(self, page_number):
    """Retrieves a specific page.

    Args:
      page_number: an integer containing the page number.

    Returns:
      An objects data page (instance of ObjectsDataPage) or None.
    """
    file_offset = page_number * ObjectsDataPage.PAGE_SIZE
    if file_offset >= self._file_size:
      return

    # TODO: cache pages.
    return self._ReadPage(file_offset)

  def _ReadClassDescriptor(self, object_record_data):
    """Reads a class descriptor object record.

    Args:
      object_record_data: a binary string containing the object record data.

    Raises:
      IOError: if the object record cannot be read.
    """
    if self._debug:
      print(u'Reading class definition object record.')

    try:
      class_definition_struct = self._CLASS_DEFINITION_OBJECT_RECORD.parse(
          object_record_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse class definition object record with '
          u'error: {0:s}').format(exception))

    try:
      utf16_stream = class_definition_struct.get(u'super_class_name_string')
      super_class_name_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      super_class_name_string = u''

    date_time = class_definition_struct.get(u'date_time')

    if self._debug:
      print(u'Super class name string size\t\t\t\t\t\t: {0:d}'.format(
          class_definition_struct.get(u'super_class_name_string_size')))
      print(u'Super class name string\t\t\t\t\t\t\t: {0:s}'.format(
          super_class_name_string))
      print(u'Unknown date and time\t\t\t\t\t\t\t: {0!s}'.format(
          FromFiletime(date_time)))

      print(u'Data size\t\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_struct.get(u'data_size')))

      print(u'')

      print(u'Data:')
      print(hexdump.Hexdump(class_definition_struct.data))

      self._ReadClassDefinitionHeader(class_definition_struct.data)

  def _ReadClassDefinitionHeader(self, class_definition_data):
    """Reads a class definition header.

    Args:
      class_definition_data: a binary string containing the class
                             definition data.

    Raises:
      IOError: if the class definition cannot be read.
    """
    if self._debug:
      print(u'Reading class definition header.')

    try:
      class_definition_header_struct = self._CLASS_DEFINITION_HEADER.parse(
          class_definition_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse class definition header with error: {0:s}').format(
              exception))

    if self._debug:
      print(u'Unknown1\t\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'unknown1')))

      print(u'Class name offset\t\t\t\t\t\t\t: 0x{0:08x}'.format(
          class_definition_header_struct.get(u'class_name_offset')))
      print(u'Default value size\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'default_value_size')))

      print(u'Super class name block size\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'super_class_name_block_size')))
      print(u'Super class name block data:')
      print(hexdump.Hexdump(class_definition_header_struct.get(
          u'super_class_name_block_data')))

      print(u'Qualifiers block size\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'qualifiers_block_size')))
      print(u'Qualifiers block data:')
      print(hexdump.Hexdump(class_definition_header_struct.get(
          u'qualifiers_block_data')))

      print(u'Property references block size\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'property_references_block_size')))
      print(u'Property references block data:')
      print(hexdump.Hexdump(class_definition_header_struct.get(
          u'property_references_block_data')))

      print(u'Number of property references\t\t\t\t\t\t\t: {0:d}'.format(
          class_definition_header_struct.get(u'number_of_propery_references')))

      # if class_definition_header_struct.super_class_name_block_size > 4:
      #   super_class_name_block_struct = class_definition_header_struct.get(
      #       u'super_class_name_block')
      #   print(u'Super class name string flags\t\t\t\t\t\t: 0x{0:02x}'.format(
      #       super_class_name_block_struct.get(u'super_class_name_string_flags')))
      #   print(u'Super class name string\t\t\t\t\t\t\t: {0:s}'.format(
      #       super_class_name_block_struct.get(u'super_class_name_string')))
      #   print(u'Super class name string size\t\t\t\t\t\t: {0:d}'.format(
      #       super_class_name_block_struct.get(u'super_class_name_string_size')))

      # print(u'')

  def _ReadInterface(self, object_record_data):
    """Reads an interface object record.

    Args:
      object_record_data: a binary string containing the object record data.

    Raises:
      IOError: if the object record cannot be read.
    """
    if self._debug:
      print(u'Reading interface object record.')

    try:
      interface_struct = self._INTERFACE_OBJECT_RECORD.parse(object_record_data)
    except construct.FieldError as exception:
      raise IOError(
          u'Unable to parse interace object record with error: {0:s}'.format(
              exception))

    try:
      utf16_stream = interface_struct.get(u'string_digest_hash')
      string_digest_hash = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      string_digest_hash = u''

    date_time1 = interface_struct.get(u'date_time1')
    date_time2 = interface_struct.get(u'date_time2')

    if self._debug:
      print(u'String digest hash\t\t\t\t\t\t\t: {0:s}'.format(
          string_digest_hash))
      print(u'Unknown data and time1\t\t\t\t\t\t\t: {0!s}'.format(
          FromFiletime(date_time1)))
      print(u'Unknown data and time2\t\t\t\t\t\t\t: {0!s}'.format(
          FromFiletime(date_time2)))

      print(u'Data size\t\t\t\t\t\t\t\t: {0:d}'.format(
          interface_struct.get(u'data_size')))

      print(u'')

      print(u'Data:')
      print(hexdump.Hexdump(interface_struct.data))

  def _ReadPage(self, file_offset):
    """Reads a page.

    Args:
      file_offset: integer containing the offset of the page relative
                   from the start of the file.

    Return:
      An objects data page (instance of ObjectsDataPage).

    Raises:
      IOError: if the page cannot be read.
    """
    objects_page = ObjectsDataPage(debug=self._debug)
    objects_page.ReadPage(self._file_object, file_offset)
    return objects_page

  def _ReadRegistration(self, object_record_data):
    """Reads a registration object record.

    Args:
      object_record_data: a binary string containing the object record data.

    Raises:
      IOError: if the object record cannot be read.
    """
    if self._debug:
      print(u'Reading registration object record.')

    try:
      registration_struct = self._REGISTRATION_OBJECT_RECORD.parse(
          object_record_data)
    except construct.FieldError as exception:
      raise IOError((
          u'Unable to parse registration object record with '
          u'error: {0:s}').format(exception))

    try:
      utf16_stream = registration_struct.get(u'name_space_string')
      name_space_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      name_space_string = u''

    try:
      utf16_stream = registration_struct.get(u'class_name_string')
      class_name_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      class_name_string = u''

    try:
      utf16_stream = registration_struct.get(u'attribute_name_string')
      attribute_name_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      attribute_name_string = u''

    try:
      utf16_stream = registration_struct.get(u'attribute_value_string')
      attribute_value_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      attribute_value_string = u''

    if self._debug:
      print(u'Name space string size\t\t\t\t\t\t\t: {0:d}'.format(
          registration_struct.get(u'name_space_string_size')))
      print(u'Name space string\t\t\t\t\t\t\t: {0:s}'.format(
          name_space_string))

      print(u'Class name string size\t\t\t\t\t\t\t: {0:d}'.format(
          registration_struct.get(u'class_name_string_size')))
      print(u'Class name string\t\t\t\t\t\t\t: {0:s}'.format(
          class_name_string))

      print(u'Attribute name string size\t\t\t\t\t\t: {0:d}'.format(
          registration_struct.get(u'attribute_name_string_size')))
      print(u'Attribute name string\t\t\t\t\t\t\t: {0:s}'.format(
          attribute_name_string))

      print(u'Attribute value string size\t\t\t\t\t\t: {0:d}'.format(
          registration_struct.get(u'attribute_value_string_size')))
      print(u'Attribute value string\t\t\t\t\t\t\t: {0:s}'.format(
          attribute_value_string))

      print(u'')

  def Close(self):
    """Closes the objects data file."""
    if self._file_object_opened_in_object:
      self._file_object.close()
    self._file_object = None

  def GetMappedPage(self, page_number):
    """Retrieves a specific mapped page.

    Args:
      page_number: an integer containing the page number.

    Returns:
      An objects data page (instance of ObjectsDataPage) or None.
    """
    mapped_page_number = self._objects_mapping_file.mappings[page_number]

    objects_page = self._GetPage(mapped_page_number)
    if not objects_page:
      logging.warning(
          u'Unable to read objects data mapped page: {0:d}.'.format(
              page_number))
      return

    return objects_page

  def GetObjectRecordByKey(self, key):
    """Retrieves a specific object record.

    Args:
      key: a string containing the CIM key.

    Returns:
      An objects data page (instance of ObjectsDataPage) or None.
    """
    _, _, key = key.rpartition(self._KEY_SEGMENT_SEPARATOR)

    if self._KEY_VALUE_SEPARATOR not in key:
      return

    key_values = key.split(self._KEY_VALUE_SEPARATOR)
    if not len(key_values) == 4:
      logging.warning(u'Unsupported number of key values.')
      return

    try:
      page_number = int(key_values[self._KEY_VALUE_PAGE_NUMBER_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value page number.')
      return

    object_page = self.GetMappedPage(page_number)
    if not object_page:
      return

    try:
      record_identifier = int(
          key_values[self._KEY_VALUE_RECORD_IDENTIFIER_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value record identifier.')
      return

    try:
      data_size = int(key_values[self._KEY_VALUE_DATA_SIZE_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value data size.')
      return

    # TODO: add support for multi page spanning record data?
    object_record_data = object_page.ReadObjectRecord(
        self._file_object, record_identifier, data_size)
    if not object_record_data:
      logging.warning(u'Unable to read objects record: {0:d}.'.format(
          record_identifier))
      return

    if self._debug:
      data_type, _, _ = key_values[0].partition(u'_')
      if data_type == u'CD':
        self._ReadClassDescriptor(object_record_data)
      elif data_type in (u'I', u'IL'):
        self._ReadInterface(object_record_data)
      elif data_type == u'R':
        self._ReadRegistration(object_record_data)

    return object_record_data

  def Open(self, filename):
    """Opens the objects data file.

    Args:
      filename: a string containing the filename.
    """
    stat_object = os.stat(filename)
    self._file_size = stat_object.st_size

    self._file_object = open(filename, 'rb')
    self._file_object_opened_in_object = True


class CIMRepository(object):
  """Class that contains a CIM repository."""

  _MAPPING_VER = construct.ULInt32(u'active_mapping_file')

  def __init__(self, debug=False):
    """Initializes the CIM repository object.

    Args:
      debug: optional boolean value to indicate if debug information should
             be printed.
    """
    super(CIMRepository, self).__init__()
    self._debug = debug
    self._index_binary_tree_file = None
    self._index_mapping_file = None
    self._objects_data_file = None
    self._objects_mapping_file = None

  def _GetCurrentMappingFile(self, path):
    """Retrieves the current mapping file.

    Args:
      path: a string containing the path to the CIM repository.
    """
    mapping_file_glob = glob.glob(
        os.path.join(path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg].[Vv][Ee][Rr]'))

    active_mapping_file = 0
    if mapping_file_glob:
      with open(mapping_file_glob[0], 'rb') as file_object:
        try:
          active_mapping_file = self._MAPPING_VER.parse_stream(file_object)
        except construct.FieldError as exception:
          raise IOError(
              u'Unable to parse Mapping.ver with error: {0:s}'.format(
                  exception))

      if self._debug:
        print(u'Active mapping file: {0:d}'.format(active_mapping_file))

    if mapping_file_glob:
      mapping_file_glob = glob.glob(os.path.join(
          path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg]{0:d}.[Mm][Aa][Pp]'.format(
              active_mapping_file)))
    else:
      mapping_file_glob = glob.glob(os.path.join(
          path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg][1-3].[Mm][Aa][Pp]'))

    # TODO: determine active mapping file for Windows Vista and later.
    for mapping_file_path in mapping_file_glob:
      if self._debug:
        print(u'Reading: {0:s}'.format(mapping_file_path))

      objects_mapping_file = MappingFile(debug=self._debug)
      objects_mapping_file.Open(mapping_file_path)

      index_mapping_file = MappingFile(debug=self._debug)
      index_mapping_file.Open(
          mapping_file_path, file_offset=objects_mapping_file.data_size)

  def _GetKeysFromIndexPage(self, index_page):
    """Retrieves the keys from an index page.

    Yields:
      A string containing the CIM key.
    """
    for key in index_page.keys:
      yield key

    for sub_page_number in index_page.sub_pages:
      sub_index_page = self._index_binary_tree_file.GetMappedPage(
          sub_page_number)
      for key in self._GetKeysFromIndexPage(sub_index_page):
        yield key

  def Close(self):
    """Closes the CIM repository."""
    if self._index_binary_tree_file:
      self._index_binary_tree_file.Close()
      self._index_binary_tree_file = None

    if self._index_mapping_file:
      self._index_mapping_file.Close()
      self._index_mapping_file = None

    if self._objects_data_file:
      self._objects_data_file.Close()
      self._objects_data_file = None

    if self._objects_mapping_file:
      self._objects_mapping_file.Close()
      self._objects_mapping_file = None

  def GetKeys(self):
    """Retrieves the keys.

    Yields:
      A string containing the CIM key.
    """
    if not self._index_binary_tree_file:
      return

    index_page = self._index_binary_tree_file.GetRootPage()
    for key in self._GetKeysFromIndexPage(index_page):
      yield key

  def GetObjectRecordByKey(self, key):
    """Retrieves a specific object record.

    Args:
      key: a string containing the CIM key.

    Returns:
      An objects data page (instance of ObjectsDataPage) or None.
    """
    if not self._objects_data_file:
      return

    return self._objects_data_file.GetObjectRecordByKey(key)

  def Open(self, path):
    """Opens the CIM repository.

    Args:
      path: a string containing the path to the CIM repository.
    """
    # TODO: self._GetCurrentMappingFile(path)

    # Index mappings file.
    index_mapping_file_path = glob.glob(
        os.path.join(path, u'[Ii][Nn][Dd][Ee][Xx].[Mm][Aa][Pp]'))[0]

    if self._debug:
      print(u'Reading: {0:s}'.format(index_mapping_file_path))

    self._index_mapping_file = MappingFile(debug=self._debug)
    self._index_mapping_file.Open(index_mapping_file_path)

    # Index binary tree file.
    index_binary_tree_file_path = glob.glob(
        os.path.join(path, u'[Ii][Nn][Dd][Ee][Xx].[Bb][Tt][Rr]'))[0]

    if self._debug:
      print(u'Reading: {0:s}'.format(index_binary_tree_file_path))

    self._index_binary_tree_file = IndexBinaryTreeFile(
        self._index_mapping_file, debug=self._debug)
    self._index_binary_tree_file.Open(index_binary_tree_file_path)

    # Objects mappings file.
    objects_mapping_file_path = glob.glob(
        os.path.join(path, u'[Oo][Bb][Jj][Ee][Cc][Tt][Ss].[Mm][Aa][Pp]'))[0]

    if self._debug:
      print(u'Reading: {0:s}'.format(objects_mapping_file_path))

    self._objects_mapping_file = MappingFile(debug=self._debug)
    self._objects_mapping_file.Open(objects_mapping_file_path)

    # Objects data file.
    objects_data_file_path = glob.glob(
        os.path.join(path, u'[Oo][Bb][Jj][Ee][Cc][Tt][Ss].[Da][Aa][Tt][Aa]'))[0]

    if self._debug:
      print(u'Reading: {0:s}'.format(objects_data_file_path))

    self._objects_data_file = ObjectsDataFile(
        self._objects_mapping_file, debug=self._debug)
    self._objects_data_file.Open(objects_data_file_path)


def Main():
  """The main program function.

  Returns:
    A boolean containing True if successful or False if not.
  """
  argument_parser = argparse.ArgumentParser(description=(
      u'Extracts information from WMI Common Information Model (CIM) '
      u'repository files.'))

  argument_parser.add_argument(
      u'-d', u'--debug', dest=u'debug', action=u'store_true', default=False,
      help=u'enable debug output.')

  argument_parser.add_argument(
      u'source', nargs=u'?', action=u'store', metavar=u'PATH',
      default=None, help=(
          u'path of the directory containing the WMI Common Information '
          u'Model (CIM) repository files.'))

  options = argument_parser.parse_args()

  if not options.source:
    print(u'Source file missing.')
    print(u'')
    argument_parser.print_help()
    print(u'')
    return False

  logging.basicConfig(
      level=logging.INFO, format=u'[%(levelname)s] %(message)s')

  cim_repository = CIMRepository(debug=options.debug)

  cim_repository.Open(options.source)
  for key in cim_repository.GetKeys():
    print(key)

    if u'.' in key:
      cim_repository.GetObjectRecordByKey(key)

  cim_repository.Close()

  return True


if __name__ == '__main__':
  if not Main():
    sys.exit(1)
  else:
    sys.exit(0)
