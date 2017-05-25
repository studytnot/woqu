# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import logging
import os
import pickle
import re

from devil import base_error
from devil.utils import cmd_helper
from pylib import constants
from pylib.base import base_test_result
from pylib.base import test_instance
from pylib.constants import host_paths


_GIT_CR_POS_RE = re.compile(r'^Cr-Commit-Position: refs/heads/master@{#(\d+)}$')


def _GetPersistedResult(test_name):
  file_name = os.path.join(constants.PERF_OUTPUT_DIR, test_name)
  if not os.path.exists(file_name):
    logging.error('File not found %s', file_name)
    return None

  with file(file_name, 'r') as f:
    return pickle.load(f)


def _GetChromiumRevision():
  # pylint: disable=line-too-long
  """Get the git hash and commit position of the chromium master branch.

  See:
  https://chromium.googlesource.com/chromium/tools/build/+/387e3cf3/scripts/slave/runtest.py#211

  Returns:
    A dictionary with 'revision' and 'commit_pos' keys.
  """
  # pylint: enable=line-too-long
  status, output = cmd_helper.GetCmdStatusAndOutput(
      ['git', 'log', '-n', '1', '--pretty=format:%H%n%B', 'HEAD'],
      cwd=host_paths.DIR_SOURCE_ROOT)
  revision = None
  commit_pos = None
  if not status:
    lines = output.splitlines()
    revision = lines[0]
    for line in reversed(lines):
      m = _GIT_CR_POS_RE.match(line.strip())
      if m:
        commit_pos = int(m.group(1))
        break
  return {'revision': revision, 'commit_pos': commit_pos}


class PerfTestInstance(test_instance.TestInstance):
  def __init__(self, args, _):
    super(PerfTestInstance, self).__init__()

    self._collect_chartjson_data = args.collect_chartjson_data
    self._collect_json_data = args.collect_json_data
    self._dry_run = args.dry_run
    self._flaky_steps = args.flaky_steps
    self._output_dir_archive_path = args.output_dir_archive_path
    # TODO(rnephew): Get rid of this when everything uses
    # --output-dir-archive-path
    if self._output_dir_archive_path is None and args.get_output_dir_archive:
      self._output_dir_archive_path = args.get_output_dir_archive
    self._known_devices_file = args.known_devices_file
    self._max_battery_temp = args.max_battery_temp
    self._min_battery_level = args.min_battery_level
    self._no_timeout = args.no_timeout
    self._output_chartjson_data = args.output_chartjson_data
    self._output_json_data = args.output_json_data
    self._output_json_list = args.output_json_list
    self._print_step = args.print_step
    self._single_step = (
        ' '.join(args.single_step_command) if args.single_step else None)
    self._steps = args.steps
    self._test_filter = args.test_filter
    self._write_buildbot_json = args.write_buildbot_json

  #override
  def SetUp(self):
    pass

  #override
  def TearDown(self):
    pass

  def OutputJsonList(self):
    try:
      with file(self._steps, 'r') as i:
        all_steps = json.load(i)

      step_values = []
      for k, v in all_steps['steps'].iteritems():
        data = {'test': k, 'device_affinity': v['device_affinity']}

        persisted_result = _GetPersistedResult(k)
        if persisted_result:
          data['start_time'] = persisted_result['start_time']
          data['end_time'] = persisted_result['end_time']
          data['total_time'] = persisted_result['total_time']
          data['has_archive'] = persisted_result['archive_bytes'] is not None
        step_values.append(data)

      with file(self.output_json_list, 'w') as o:
        o.write(json.dumps(step_values))
      return base_test_result.ResultType.PASS
    except KeyError:
      logging.exception('Persistent results file missing key.')
      return base_test_result.ResultType.FAIL

  def PrintTestOutput(self):
    """Helper method to print the output of previously executed test_name.

    Test_name is passed from the command line as print_step

    Returns:
      exit code generated by the test step.
    """
    persisted_result = _GetPersistedResult(self._print_step)
    if not persisted_result:
      raise PersistentDataError('No data for test %s found.' % self._print_step)
    logging.info('*' * 80)
    logging.info('Output from:')
    logging.info(persisted_result['cmd'])
    logging.info('*' * 80)

    output_formatted = ''
    persisted_outputs = persisted_result['output']
    for i in xrange(len(persisted_outputs)):
      output_formatted += '\n\nOutput from run #%d:\n\n%s' % (
          i, persisted_outputs[i])
    print output_formatted

    if self.output_json_data:
      with file(self.output_json_data, 'w') as f:
        f.write(persisted_result['json'])

    if self.output_chartjson_data:
      with file(self.output_chartjson_data, 'w') as f:
        f.write(persisted_result['chartjson'])

    if self.output_dir_archive_path:
      if persisted_result['archive_bytes'] is not None:
        with file(self.output_dir_archive_path, 'wb') as f:
          f.write(persisted_result['archive_bytes'])
      else:
        logging.error('The output dir was not archived.')
    if persisted_result['exit_code'] == 0:
      return base_test_result.ResultType.PASS
    return base_test_result.ResultType.FAIL

  #override
  def TestType(self):
    return 'perf'

  @staticmethod
  def ReadChartjsonOutput(output_dir):
    if not output_dir:
      return ''
    json_output_path = os.path.join(output_dir, 'results-chart.json')
    try:
      with open(json_output_path) as f:
        return f.read()
    except IOError:
      logging.exception('Exception when reading chartjson.')
      logging.error('This usually means that telemetry did not run, so it could'
                    ' not generate the file. Please check the device running'
                    ' the test.')
      return ''

  @staticmethod
  def ReadJsonOutput(output_dir):
    if not output_dir:
      return ''
    json_output_path = os.path.join(output_dir, 'results.json')
    try:
      with open(json_output_path) as f:
        return f.read()
    except IOError:
      logging.exception('Exception when reading results.json.')
      logging.error('This usually means that telemetry did not run, so it could'
                    ' not generate the file. Please check the device running'
                    ' the test.')
      return ''

  def WriteBuildBotJson(self, output_dir):
    """Write metadata about the buildbot environment to the output dir."""
    if not output_dir or not self._write_buildbot_json:
      return
    data = {
      'chromium': _GetChromiumRevision(),
      'environment': dict(os.environ)
    }
    with open(os.path.join(output_dir, 'buildbot.json'), 'w') as f:
      json.dump(data, f, sort_keys=True, separators=(',', ': '))

  @property
  def collect_chartjson_data(self):
    return self._collect_chartjson_data

  @property
  def collect_json_data(self):
    return self._collect_json_data

  @property
  def dry_run(self):
    return self._dry_run

  @property
  def flaky_steps(self):
    return self._flaky_steps

  @property
  def known_devices_file(self):
    return self._known_devices_file

  @property
  def max_battery_temp(self):
    return self._max_battery_temp

  @property
  def min_battery_level(self):
    return self._min_battery_level

  @property
  def no_timeout(self):
    return self._no_timeout

  @property
  def output_chartjson_data(self):
    return self._output_chartjson_data

  @property
  def output_dir_archive_path(self):
    return self._output_dir_archive_path

  @property
  def output_json_data(self):
    return self._output_json_data

  @property
  def output_json_list(self):
    return self._output_json_list

  @property
  def print_step(self):
    return self._print_step

  @property
  def single_step(self):
    return self._single_step

  @property
  def steps(self):
    return self._steps

  @property
  def test_filter(self):
    return self._test_filter


class PersistentDataError(base_error.BaseError):
  def __init__(self, message):
    super(PersistentDataError, self).__init__(message)
    self._is_infra_error = True
