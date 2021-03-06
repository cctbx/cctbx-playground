
"""
Library of convenience functions for working with models and reflection data.
This contains a number of routines used in phenix.refine and related programs,
mainly concerned with the repetitive process of loading model and data files
and initializing the appropriate objects.  Note that if you are writing a
program that uses similar inputs, it may be significantly easier to use the
unified input handling encapsulated in :py:mod:`mmtbx.command_line`, which
wraps much of the functionality in :py:mod:`mmtbx.utils` while hiding the
messy details.
"""

from __future__ import division
from mmtbx.scaling import twin_analyses
from cctbx import miller
from cctbx import crystal
from cctbx import sgtbx
import cctbx.xray.structure_factors
from cctbx.array_family import flex
from libtbx.utils import \
  Sorry, date_and_time, host_and_user, multi_out, null_out
import iotbx.phil
from iotbx import reflection_file_utils
from iotbx import crystal_symmetry_from_any
from iotbx.pdb import xray_structure
from iotbx import pdb
from cStringIO import StringIO
from cctbx import adptbx
from mmtbx import monomer_library
import mmtbx.monomer_library.pdb_interpretation
import mmtbx.monomer_library.server
from iotbx.pdb import combine_unique_pdb_files
from iotbx import mtz
from iotbx import cif
from libtbx import str_utils
from libtbx.str_utils import show_string
from libtbx import adopt_init_args
import random, sys, os
from libtbx.test_utils import approx_equal
from mmtbx.refinement import print_statistics
from mmtbx.twinning import twin_f_model
import mmtbx.bulk_solvent.bulk_solvent_and_scaling as bss
import mmtbx.f_model
import mmtbx.restraints
import mmtbx.tls.tools
from mmtbx.scaling import outlier_rejection
import mmtbx.command_line.fmodel
from cctbx import french_wilson
import math
import libtbx.callbacks # import dependency
from libtbx.math_utils import ifloor, iceil
from cctbx import maptbx
from cctbx import uctbx
from cctbx import xray
from iotbx.cns.miller_array import crystal_symmetry_as_cns_comments
from iotbx.file_reader import any_file
from mmtbx.rotamer.rotamer_eval import RotamerEval

import boost.python
utils_ext = boost.python.import_ext("mmtbx_utils_ext")
from mmtbx_utils_ext import *

import boost.python
from mmtbx import bulk_solvent
ext = boost.python.import_ext("mmtbx_f_model_ext")

def miller_array_symmetry_safety_check(miller_array,
                                       data_description,
                                       working_point_group,
                                       symmetry_safety_check,
                                       log):
  msg = miller_array.crystal_symmetry_is_compatible_with_symmetry_from_file(
    working_point_group = working_point_group).format_error_message(
      data_description = data_description)
  if(msg is not None):
     if(symmetry_safety_check == "warning"):
        print >> log, "*" * 79
        print >> log, "WARNING:", msg
        print >> log, "*" * 79
     else:
        raise Sorry(msg + """
  The program inspects all inputs to determine the working crystal
  symmetry (unit cell & space group).
  Please check the working crystal symmetry shown above. If it is
  not correct, use the --unit_cell, --space_group, or --symmetry
  option to specify the correct unit cell parameters and space group
  symbol.
  If the working crystal symmetry is in fact correct, disable this
  error by adding
    refinement.input.symmetry_safety_check=warning
  to the command line arguments.
""")

def explain_how_to_generate_array_of_r_free_flags(log, flags_parameter_scope):
  part1 = """\
If previously used R-free flags are available run this command again
with the name of the file containing the original flags as an
additional input. If the structure was never refined before, or if the
original R-free flags are unrecoverable, run this command again with
the additional definition:

"""
  part3 = """

If the structure was refined previously using different R-free flags,
the values for R-free will become meaningful only after many cycles of
refinement.
"""
  print >> log, part1 + flags_parameter_scope+""".generate=True""" + part3

data_and_flags_str_part1 = """\
  file_name = None
    .type=path
    .short_caption=Reflections file
    .style = bold input_file file_type:hkl noauto process_hkl \
      child:fobs:labels child:d_min:high_resolution \
      child:d_max:low_resolution child:rfree_file:r_free_flags.file_name
    .expert_level = 0
  labels = None
    .type=strings
    .input_size = 160
    .short_caption = Data labels
    .style = bold renderer:draw_fobs_label_widget noauto \
      OnChange:auto_update_label_choice child:d_min:high_resolution \
      child:d_max:low_resolution parent:file_name:file_name
    .expert_level = 0
  high_resolution = None
    .type=float
    .input_size = 80
    .style = bold renderer:draw_resolution_widget noauto
    .expert_level = 0
  low_resolution = None
    .type=float
    .input_size = 80
    .style = bold renderer:draw_resolution_widget noauto
    .expert_level = 0
  outliers_rejection = True
    .type=bool
    .short_caption = Reject outliers
    .help = Remove "basic wilson outliers", "extreme wilson outliers", and \
              "beamstop shadow outliers"
    .expert_level = 0
  french_wilson_scale = True
    .type=bool
    .short_caption = use French-Wilson method to handle negative intensities
  french_wilson
  {
     include scope cctbx.french_wilson.master_phil
  }
  sigma_fobs_rejection_criterion = None
    .type=float
    .short_caption = Sigma(Fobs) rejection criterion
    .expert_level = 0
  sigma_iobs_rejection_criterion = None
    .type=float
    .short_caption = Sigma(Iobs) rejection criterion
    .expert_level = 0
"""

data_and_flags_str_part2 = """\
  file_name = None
    .type=path
    .short_caption=File with R(free) flags
    .help = This is normally the same as the file containing Fobs and is \
      usually selected automatically.
    .input_size = 200
    .style = noauto input_file file_type:hkl process_hkl child:rfree:label
    .expert_level = 0
  label = None
    .type=str
    .short_caption = R-free label
    .input_size = 160
    .style = bold renderer:draw_rfree_label_widget noauto \
             OnChange:update_rfree_flag_value
    .expert_level = 0
  test_flag_value = None
    .type=int
    .help = This value is usually selected automatically - do not change \
      unless you really know what you're doing!
    .style = noauto
    .expert_level = 0
  ignore_r_free_flags = False
    .type=bool
    .short_caption = Ignore R-free flags
    .help = Use all reflections in refinement (work and test)
    .expert_level=0
"""

data_and_flags_str = """\
  %s
  ignore_all_zeros = True
    .type=bool
    .short_caption = Ignore all-zero arrays
    .expert_level = 1
  force_anomalous_flag_to_be_equal_to = None
    .type=bool
    .short_caption = Use anomalous data
    .style = tribool
    .expert_level = 1
  convert_to_non_anomalous_if_ratio_pairs_lone_less_than_threshold=0.5
    .type = float
    .expert_level = 2
  r_free_flags
    .expert_level=0
    .style = box auto_align
    .caption = This information will be extracted automatically if possible. \
      If no test set is present in the reflections file, one can be generated \
      automatically, or you can use the reflection file editor to combine an \
      existing set with your X-ray or neutron data.
  {
    %s
    disable_suitability_test = False
      .type=bool
      .expert_level = 2
    ignore_pdb_hexdigest = False
      .type=bool
      .short_caption = Ignore PDB hexdigest sanity check
      .help=If True, disables safety check based on MD5 hexdigests stored in \
            PDB files produced by previous runs.
      .expert_level=2
    generate = False
      .type=bool
      .short_caption = Generate new R-free flags
      .help = Generate R-free flags (if not available in input files)
    %s
  }
""" % (data_and_flags_str_part1,
       data_and_flags_str_part2,
       miller.generate_r_free_params_str)

xray_data_str = """\
xray_data
  .help=Scope of X-ray data and free-R flags
  .style = scrolled auto_align
{
  %s
}
"""%data_and_flags_str

neutron_data_str = """\
neutron_data
  .help=Scope of neutron data and neutron free-R flags
  .style = scrolled auto_align
{
  ignore_xn_free_r_mismatch = False
    .type = bool
    .expert_level=2
    .short_caption = Ignore Xray/neutron R-free flags set mismatch
  %s
}

"""%data_and_flags_str

def data_and_flags_master_params(master_scope_name=None):
  if(master_scope_name is not None):
    p = """\
%s
{
%s
}
"""
    return iotbx.phil.parse(p%(master_scope_name, data_and_flags_str), process_includes=True)
  else:
    return iotbx.phil.parse(data_and_flags_str, process_includes=True)

class determine_data_and_flags(object):
  """
  Encapsulates logic for extracting experimental amplitudes and R-free flags
  from the given input file(s).  This expects that the standard parameter block
  is being used.  Determination of appropriate data labels will be as automatic
  as possible, or will give clear feedback when ambiguity exists.  If not
  found in the inputs, the R-free flags can be created if desired.
  """
  def __init__(self, reflection_file_server,
                     parameters = None,
                     data_parameter_scope = "",
                     flags_parameter_scope = "",
                     data_description = None,
                     working_point_group = None,
                     symmetry_safety_check = None,
                     remark_r_free_flags_md5_hexdigest = None,
                     extract_r_free_flags = True,
                     keep_going = False,
                     log = None,
                     prefer_anomalous = None,
                     force_non_anomalous = False):
    adopt_init_args(self, locals())
    if(self.parameters is None):
      self.parameters = data_and_flags_master_params().extract()
    self.intensity_flag = False
    self.f_obs = None
    self.r_free_flags = None
    self.test_flag_value = None
    self.r_free_flags_md5_hexdigest = None
    if(data_description is not None):
      print_statistics.make_header(data_description, out = log)
    self.raw_data = self.extract_data()
    data_info = self.raw_data.info()
    self.f_obs = self.data_as_f_obs(f_obs = self.raw_data)
    self.f_obs.set_info(data_info)
    if(extract_r_free_flags):
      self.raw_flags = self.extract_flags(data = self.raw_data)
      if(self.raw_flags is not None):
        flags_info = self.raw_flags.info()
    if(extract_r_free_flags and self.raw_flags is not None):
      self.get_r_free_flags()
      self.r_free_flags.set_info(flags_info)

  def get_r_free_flags(self):
    self.r_free_flags,self.test_flag_value,self.r_free_flags_md5_hexdigest =\
      self.flags_as_r_free_flags(f_obs = self.f_obs, r_free_flags =
      self.raw_flags)
    self.r_free_flags.set_info(self.raw_flags.info())

  def extract_data(self):
    data = self.reflection_file_server.get_xray_data(
      file_name        = self.parameters.file_name,
      labels           = self.parameters.labels,
      ignore_all_zeros = self.parameters.ignore_all_zeros,
      parameter_scope  = self.data_parameter_scope,
      prefer_anomalous = self.prefer_anomalous)
    self.parameters.file_name = data.info().source
    self.parameters.labels = [data.info().label_string()]
    if(data.is_xray_intensity_array()):
      print >> self.log, "I-obs:"
      self.intensity_flag = True
    else:
      print >> self.log, "F-obs:"
    print >> self.log, " ", data.info()
    if([self.data_description, self.working_point_group,
       self.symmetry_safety_check].count(None) == 0):
      miller_array_symmetry_safety_check(
        miller_array          = data,
        data_description      = self.data_description,
        working_point_group   = self.working_point_group,
        symmetry_safety_check = self.symmetry_safety_check,
        log                   = self.log)
      print >> self.log
    info = data.info()
    processed = data.eliminate_sys_absent(log = self.log)
    if(processed is not data):
      info = info.customized_copy(systematic_absences_eliminated = True)
    if(not processed.is_unique_set_under_symmetry()):
      if(data.is_xray_intensity_array()):
        print >> self.log, "Merging symmetry-equivalent intensities:"
      else:
        print >> self.log, "Merging symmetry-equivalent amplitudes:"
      merged = processed.merge_equivalents()
      merged.show_summary(out = self.log, prefix="  ")
      print >> self.log
      processed = merged.array()
      info = info.customized_copy(merged=True)
    if (self.force_non_anomalous) :
      processed = processed.average_bijvoet_mates()
    return processed.set_info(info)

  def extract_flags(self, data, data_description = "R-free flags"):
    r_free_flags, test_flag_value = None, None
    params = self.parameters.r_free_flags
    if(not self.parameters.r_free_flags.generate):
      try:
        r_free_flags, test_flag_value = \
          self.reflection_file_server.get_r_free_flags(
            file_name                = params.file_name,
            label                    = params.label,
            test_flag_value          = params.test_flag_value,
            disable_suitability_test = params.disable_suitability_test,
            parameter_scope          = self.flags_parameter_scope)
      except reflection_file_utils.Sorry_No_array_of_the_required_type, e:
        if(self.parameters.r_free_flags.generate is not None):
          explain_how_to_generate_array_of_r_free_flags(log = self.log,
            flags_parameter_scope = self.flags_parameter_scope)
          if(self.keep_going): return None
          raise Sorry("Please try again.")
        r_free_flags, test_flag_value = None, None
      else:
        params.file_name = r_free_flags.info().source
        params.label = r_free_flags.info().label_string()
        params.test_flag_value = test_flag_value
        print >> self.log, data_description+":"
        print >> self.log, " ", r_free_flags.info()
        if([self.working_point_group,
           self.symmetry_safety_check].count(None) == 0):
          miller_array_symmetry_safety_check(
            miller_array          = r_free_flags,
            data_description      = data_description,
            working_point_group   = self.working_point_group,
            symmetry_safety_check = self.symmetry_safety_check,
            log                   = self.log)
          print >> self.log
        info = r_free_flags.info()
        processed = r_free_flags.eliminate_sys_absent(log = self.log)
        if(processed is not r_free_flags):
          info = info.customized_copy(systematic_absences_eliminated = True)
        if(not processed.is_unique_set_under_symmetry()):
           print >> self.log, \
             "Checking symmetry-equivalent R-free flags for consistency:",
           try:
             merged = processed.merge_equivalents()
           except RuntimeError, e:
             print >> self.log
             error_message = str(e)
             expected_error_message = "cctbx Error: merge_equivalents_exact: "
             assert error_message.startswith(expected_error_message)
             raise Sorry("Incompatible symmetry-equivalent R-free flags: %s" %
               error_message[len(expected_error_message):])
           else:
             print >> self.log, "OK"
             print >> self.log
           processed = merged.array()
           info = info.customized_copy(merged=True)
           del merged
        if (self.force_non_anomalous) :
          processed = processed.average_bijvoet_mates()
        r_free_flags = processed.set_info(info)
    if(r_free_flags is None):
      if ((params.fraction is None) or
          (params.lattice_symmetry_max_delta is None) or
          (params.use_lattice_symmetry is None)) :
        raise Sorry("No R-free flags are available, but one or more "+
          "parameters required to generate new flags is undefined.")
      print >> self.log, "Generating a new array of R-free flags."
      print >> self.log
      libtbx.call_back(message="warn",
        data="PHENIX will generate a new array of R-free flags.  Please "+
          "check to make sure that the input data do not already contain "+
          "an R-free set; if one is present, you should cancel this job and "+
          "disable generation of new flags.  If the program you are running "+
          "outputs an MTZ file, you should be sure to use that file in all "+
          "future refinements.")
      r_free_flags = data.generate_r_free_flags(
        fraction                   = params.fraction,
        max_free                   = params.max_free,
        lattice_symmetry_max_delta = params.lattice_symmetry_max_delta,
        use_lattice_symmetry       = params.use_lattice_symmetry,
        use_dataman_shells         = params.use_dataman_shells,
        n_shells                   = params.n_shells
        ).set_info(miller.array_info(labels = ["R-free-flags"]))
      params.label = r_free_flags.info().label_string()
      params.test_flag_value = 1
    # check if anomalous pairs are sound
    if(r_free_flags is not None):
      r_free_flags.deep_copy().as_non_anomalous_array()
    return r_free_flags

  def data_as_f_obs(self, f_obs):
    """
    Convert input data array to amplitudes, adjusting the data type and
    applying additional filters if necessary.

    :param f_obs: selected input data
    :returns: :py:class:`cctbx.miller.array` of real numbers with observation
      type set to amplitudes
    """
    if(not f_obs.sigmas_are_sensible()):
      f_obs = f_obs.customized_copy(
        indices=f_obs.indices(),
        data=f_obs.data(),
        sigmas=None).set_observation_type(f_obs)
    # Delete F(0,0,0) if present
    sel = f_obs.indices()==(0,0,0)
    if(sel.count(True)>0):
      print >> self.log, "F(0,0,0) will be removed."
      f_obs = f_obs.select(~sel)
    #
    d_min = f_obs.d_min()
    if(d_min < 0.25): # XXX what is the equivalent for neutrons ???
      raise Sorry("Resolution of data is too high: %-6.4f A"%d_min)
    f_obs.show_comprehensive_summary(f = self.log)
    f_obs_data_size = f_obs.data().size()
    print >> self.log
    if(f_obs.is_complex_array()): f_obs = abs(f_obs)
    f_obs_fw = None
    if(f_obs.is_xray_intensity_array()):
      if(self.parameters.french_wilson_scale) :
        f_obs_fw = french_wilson.french_wilson_scale(
          miller_array=f_obs,
          params=self.parameters.french_wilson,
          sigma_iobs_rejection_criterion=\
            self.parameters.sigma_iobs_rejection_criterion,
          log=self.log)
        if f_obs_fw is not None:
          f_obs = f_obs_fw
      if (not self.parameters.french_wilson_scale or f_obs_fw is None) :
        selection_by_isigma = self._apply_sigma_cutoff(
          f_obs   = f_obs,
          n       = self.parameters.sigma_iobs_rejection_criterion,
          message = "Number of reflections with |Iobs|/sigma(Iobs) < %5.2f: %d")
        if(selection_by_isigma is not None):
          f_obs = f_obs.select(selection_by_isigma)
        f_obs = f_obs.f_sq_as_f()
      print >> self.log, \
        "Intensities converted to amplitudes for use in refinement."
      print >> self.log
    #
    sigmas = f_obs.sigmas()
    if(sigmas is not None):
      selection  = sigmas > 0
      selection &= f_obs.data()>=0
      n_both_zero = selection.count(False)
      if(n_both_zero>0):
        print >> self.log, \
          "Number of pairs (Fobs,sigma)=(0,0) is %s. They will be removed"%\
          n_both_zero
        f_obs = f_obs.select(selection)
    #
    f_obs.set_observation_type_xray_amplitude()
    f_obs = f_obs.map_to_asu()
    selection = f_obs.all_selection()
    if(self.parameters.low_resolution is not None):
      selection &= f_obs.d_spacings().data() <= self.parameters.low_resolution
    if(self.parameters.high_resolution is not None):
      selection &= f_obs.d_spacings().data() >= self.parameters.high_resolution
    selection_positive = f_obs.data() >= 0
    print >> self.log, \
      "Number of F-obs in resolution range:                  ", \
      selection.count(True)
    print >> self.log, \
      "Number of F-obs<0 (these reflections will be rejected):", \
      selection_positive.count(False)
    selection_zero = f_obs.data() == 0
    print >> self.log, \
      "Number of F-obs=0 (these reflections will be used in refinement):", \
      selection_zero.count(True)
    selection &= selection_positive
    selection_by_fsigma = self._apply_sigma_cutoff(
      f_obs   = f_obs,
      n       = self.parameters.sigma_fobs_rejection_criterion,
      message = "Number of reflections with |Fobs|/sigma(Fobs) < %5.2f: %d")
    if(selection_by_fsigma is not None): selection &= selection_by_fsigma
    selection &= f_obs.d_star_sq().data() > 0
    f_obs = f_obs.select(selection)
    rr = f_obs.resolution_range()
    print >> self.log, "Refinement resolution range: d_max = %8.4f" % rr[0]
    print >> self.log, "                             d_min = %8.4f" % rr[1]
    print >> self.log
    if(f_obs.indices().size() == 0):
      raise Sorry(
        "No data left after applying resolution limits and sigma cutoff.")
    if(self.parameters.force_anomalous_flag_to_be_equal_to is not None):
      if(not self.parameters.force_anomalous_flag_to_be_equal_to):
        print >> self.log, "force_anomalous_flag_to_be_equal_to=False"
        if(f_obs.anomalous_flag()):
          print >> self.log, "Reducing data to non-anomalous array."
          merged = f_obs.as_non_anomalous_array().merge_equivalents()
          merged.show_summary(out = self.log, prefix="  ")
          f_obs = merged.array().set_observation_type( f_obs )
          del merged
          print >> self.log
      elif(not f_obs.anomalous_flag()):
        print >> self.log, "force_anomalous_flag_to_be_equal_to=True"
        print >> self.log, "Generating Bijvoet mates of X-ray data."
        observation_type = f_obs.observation_type()
        f_obs = f_obs.generate_bijvoet_mates()
        f_obs.set_observation_type(observation_type)
        print >> self.log
    else:
      f_obs = f_obs.convert_to_non_anomalous_if_ratio_pairs_lone_less_than(
        threshold=self.parameters.
          convert_to_non_anomalous_if_ratio_pairs_lone_less_than_threshold)
    if(f_obs_data_size != f_obs.data().size()):
      print >> self.log, "\nFobs statistics after all cutoffs applied:\n"
      f_obs.show_comprehensive_summary(f = self.log)
    return f_obs

  def _apply_sigma_cutoff(self, f_obs, n, message):
    selection = None
    if(f_obs.sigmas() is not None):
      sigma_cutoff = n
      if(sigma_cutoff is not None and sigma_cutoff > 0):
        selection_by_sigma = f_obs.data() > f_obs.sigmas()*sigma_cutoff
        print >> self.log, message % (sigma_cutoff,
          selection_by_sigma.count(False))
        selection = selection_by_sigma
    return selection

  def flags_as_r_free_flags(self,
        f_obs,
        r_free_flags,
        missing_show_max_lines=10):
    test_flag_value = self.parameters.r_free_flags.test_flag_value
    if (test_flag_value is None) :
      raise Sorry(("PHENIX could not determine an appropriate test flag "+
        "for the data with label(s) '%s'.  This may happen if they are all "+
        "a single value; please check the file to make sure the flags are "+
        "suitable for use.") % self.parameters.r_free_flags.label)
    r_free_flags.show_comprehensive_summary(f = self.log)
    print >> self.log
    print >> self.log, "Test (R-free flags) flag value:", test_flag_value
    print >> self.log
    if (isinstance(r_free_flags.data(), flex.bool)):
      r_free_flags = r_free_flags.array(
        data = r_free_flags.data() == bool(test_flag_value))
    else:
      r_free_flags = r_free_flags.array(
        data = r_free_flags.data() == test_flag_value)
    r_free_flags_md5_hexdigest = \
      r_free_flags.map_to_asu().sort(by_value="packed_indices").data() \
        .md5().hexdigest()
    if(self.remark_r_free_flags_md5_hexdigest is not None):
      self.verify_r_free_flags_md5_hexdigest(
        ignore_pdb_hexdigest = self.parameters.r_free_flags.ignore_pdb_hexdigest,
        current              = r_free_flags_md5_hexdigest,
        records              = self.remark_r_free_flags_md5_hexdigest)
    if(not f_obs.anomalous_flag()):
      if(r_free_flags.anomalous_flag()):
        print >> self.log, "Reducing R-free flags to non-anomalous array."
        r_free_flags = r_free_flags.average_bijvoet_mates()
        print >> self.log
    elif(not r_free_flags.anomalous_flag()):
       print >> self.log, "Generating Bijvoet mates of R-free flags."
       r_free_flags = r_free_flags.generate_bijvoet_mates()
       print >> self.log
    r_free_flags = r_free_flags.map_to_asu().common_set(f_obs)
    n_missing_r_free_flags = f_obs.indices().size() \
      - r_free_flags.indices().size()
    if(n_missing_r_free_flags != 0):
      msg = [
        "R-free flags not compatible with F-obs array:"
        " missing flag for %d F-obs selected for refinement"
          % n_missing_r_free_flags]
      if (missing_show_max_lines is not None and missing_show_max_lines <= 0):
        msg[0] += "."
      else:
        msg[0] += ":"
        lone = f_obs.lone_set(other=r_free_flags)
        if (missing_show_max_lines is None):
          n_not_shown = 0
        else:
          n_not_shown = lone.indices().size() - missing_show_max_lines
          if (n_not_shown > missing_show_max_lines * 0.5):
            lone = lone[:missing_show_max_lines]
          else:
            n_not_shown = 0
        if (lone.sigmas() is None):
          msg.append("    h   k   l   data")
          for hkl,f in zip(lone.indices(), lone.data()):
            msg.append("  %3d %3d %3d" % hkl + "   %.6g" % f)
        else:
          msg.append("    h   k   l   data  sigmas")
          for hkl,f,s in zip(lone.indices(), lone.data(), lone.sigmas()):
            msg.append("  %3d %3d %3d" % hkl + "   %.6g  %.6g" % (f,s))
        if (n_not_shown != 0):
          msg.append("    ... (remaining %d not shown)" % n_not_shown)
      raise Sorry("\n".join(msg))
    r_free_flags.show_r_free_flags_info(out = self.log, prefix="")
    return r_free_flags, test_flag_value, r_free_flags_md5_hexdigest

  def verify_r_free_flags_md5_hexdigest(self,
        ignore_pdb_hexdigest,
        current,
        records):
    from_file = set()
    for record in records:
      flds = record.split()
      if (len(flds) == 3):
        from_file.add(flds[2])
    if (len(from_file) > 1):
      raise Sorry(
        "Multiple conflicting REMARK r_free_flags.md5.hexdigest records"
        " found in the input PDB file.")
    if (len(from_file) == 1 and current not in from_file):
      log = self.log
      for i in xrange(2): print >> log, "*"*79
      if (ignore_pdb_hexdigest):
        print >> log
        print >> log, " ".join(["WARNING"]*9)
      print >> log, """
The MD5 checksum for the R-free flags array summarized above is:
  %s

The corresponding MD5 checksum in the PDB file summarized above is:
  %s

These checksums should be identical but are in fact different. This is
because the R-free flags used at previous stages of refinement are
different from the R-free flags summarized above. As a consequence,
the values for R-free could be biased and misleading.

However, there is no problem if the R-free flags were just extended to
a higher resolution, or if some reflections with no data or that are
not part of the R-free set have been added or removed.""" % (
  current, sorted(from_file)[0]),
      if (not ignore_pdb_hexdigest):
        print >> log, """\
In this case,
simply remove the

  REMARK r_free_flags.md5.hexdigest %s

record from the input PDB file to proceed with the refinement.""" % (
  sorted(from_file)[0]),
      print >> log, """

Otherwise it is best to recover the previously used R-free flags
and use them consistently throughout the refinement of the model.
Run this command again with the name of the file containing the
original flags as an additional input.
"""
      if (not ignore_pdb_hexdigest):
        print >> log, """\
If the original R-free flags are unrecoverable, remove the REMARK
record as indicated above. In this case the values for R-free will
become meaningful only after many cycles of refinement.
"""
      else:
        print >> log, """\
If the original R-free flags are unrecoverable, the values for R-free
will become meaningful only after many cycles of refinement.
"""
      for i in xrange(2): print >> log, "*"*79
      print >> log
      if (not ignore_pdb_hexdigest):
        if ("PHENIX_GUI_ENVIRONMENT" in os.environ) :
          log.flush()
          raise Sorry("This model appears to have previously been refined "+
            "against a different set of R-free flags.  Please resolve the "+
            "mismatch; additional information and instructions are available "+
            "at the end of the log output.")
        else :
          raise Sorry("Please resolve the R-free flags mismatch.")

map_coefficents_params_str = """\
  file_name=None
    .type=path
    .short_caption=Map coefficients file
  labels=None
    .type=strings
"""

experimental_phases_params_str = """\
  file_name=None
    .type=path
    .short_caption=Experimental phase file
    .style = bold input_file file_type:hkl process_hkl child:hl_coeffs:labels
  labels=None
    .type=strings
    .input_size = 160
    .short_caption = Phase labels
    .style = renderer:draw_hl_label_widget bold
"""

experimental_phases_params = iotbx.phil.parse(
  input_string=experimental_phases_params_str)

def determine_experimental_phases(reflection_file_server,
                                  parameters,
                                  log,
                                  parameter_scope,
                                  working_point_group,
                                  symmetry_safety_check,
                                  ignore_all_zeros = True):
  """
  Extract experimental phases from the given inputs if possible.  Returns None
  if not found.
  """
  try:
    experimental_phases = \
      reflection_file_server.get_experimental_phases(
        file_name        = parameters.file_name,
        labels           = parameters.labels,
        ignore_all_zeros = ignore_all_zeros,
        parameter_scope  = parameter_scope)
  except reflection_file_utils.Sorry_No_array_of_the_required_type:
    experimental_phases = None
  else:
    parameters.file_name = experimental_phases.info().source
    parameters.labels = [experimental_phases.info().label_string()]
    print >> log, "Experimental phases:"
    print >> log, " ", experimental_phases.info()
    miller_array_symmetry_safety_check(
      miller_array          = experimental_phases,
      data_description      = "Experimental phases",
      working_point_group   = working_point_group,
      symmetry_safety_check = symmetry_safety_check,
      log                   = log)
    print >> log
    info = experimental_phases.info()
    processed = experimental_phases.eliminate_sys_absent(log = log)
    if(processed is not experimental_phases):
       info = info.customized_copy(systematic_absences_eliminated = True)
    if(not processed.is_unique_set_under_symmetry()):
       print >> log, \
         "Merging symmetry-equivalent Hendrickson-Lattman coefficients:"
       merged = processed.merge_equivalents()
       merged.show_summary(out = log, prefix="  ")
       print >> log
       processed = merged.array()
       info = info.customized_copy(merged = True)
    return processed.set_info(info)

pdb_params = iotbx.phil.parse("""\
  file_name=None
    .optional=True
    .type=path
    .help=Model file(s) name (PDB)
    .short_caption=Input model
    .multiple=True
    .input_size=400
    .style = bold input_file file_type:pdb OnChange:extract_pdb_params \
      file_type_default
""")

def find_overlapping_selections (selections, selection_strings) :
  """
  Given a list of atom selections (:py:class:`scitbx.array_family.flex.bool`
  arrays) and corresponding selection strings, inspect the selections to
  determine whether any two arrays overlap.  Returns a tuple of the first pair
  of selection strings found to overlap, or None if all selections are unique.
  """
  assert (len(selections) == len(selection_strings))
  for i_sel in range(len(selections) - 1) :
    selection1 = selections[i_sel]
    for j_sel in range(i_sel + 1, len(selections)) :
      selection2 = selections[j_sel]
      if (isinstance(selection1, flex.bool)) :
        joint_sel = selection1 & selection2
        if (joint_sel.count(True) > 0) :
          return (selection_strings[i_sel], selection_strings[j_sel])
      else :
        intersection = selection1.intersection(selection2)
        if (len(intersection) > 0) :
          return (selection_strings[i_sel], selection_strings[j_sel])
  return None

def get_atom_selections(all_chain_proxies,
                        xray_structure,
                        selection_strings     = None,
                        iselection            = True,
                        one_group_per_residue = False,
                        allow_empty_selection = False,
                        hydrogens_only        = False,
                        one_selection_array   = False,
                        parameter_name        = None):
  atoms = all_chain_proxies.pdb_atoms
  scatterers = xray_structure.scatterers()
  assert atoms.size() == scatterers.size()
  for atom, sc in zip(atoms, scatterers):
    if (len(atom.element.strip()) == 0):
      e,c = sc.element_and_charge_symbols()
      if (len(e) != 0):
        atom.element = "%2s" % e.upper()
        atom.charge = "%-2s" % c.upper()
  #
  if(hydrogens_only):
    assert xray_structure is not None
  if(selection_strings is None or isinstance(selection_strings, str)):
    selection_strings = [selection_strings]
  elif (len(selection_strings) == 0):
    selection_strings = [None]
  n_none = selection_strings.count(None)
  ss_size = len(selection_strings)
  if((one_group_per_residue and n_none==0) or (ss_size > 1 and n_none > 0)):
    raise Sorry('Ambiguous selection.') # XXX NEED MORE INFORMATIVE MESSAGE
  selections = []
  if(ss_size == 1 and n_none == 1 and not one_group_per_residue):
    selections.append(flex.bool(all_chain_proxies.pdb_atoms.size(), True))
  elif(one_group_per_residue and ss_size == 1 and n_none == 1):
    assert iselection
    residues = []
    hd_selection = None
    if (hydrogens_only):
      scat_types = xray_structure.scatterers().extract_scattering_types()
      hd_selection = (scat_types == "H") | (scat_types == "D")
      if (hd_selection.count(True) == 0):
        raise Sorry('No hydrogens to select.')
    for model in all_chain_proxies.pdb_hierarchy.models():
      for chain in model.chains():
        for rg in chain.residue_groups():
          rg_i_seqs = []
          for ag in rg.atom_groups():
            for atom in ag.atoms():
              i_seq = atom.i_seq
              if (   not hydrogens_only
                  or scat_types[i_seq] in ["H", "D"]):
                rg_i_seqs.append(atom.i_seq)
          if (len(rg_i_seqs) != 0):
            selections.append(flex.size_t(rg_i_seqs))
  elif(ss_size != 1 or n_none == 0 and not one_group_per_residue):
    for selection_string in selection_strings:
      selections.append(atom_selection(all_chain_proxies = all_chain_proxies,
                                       string            = selection_string,
                                       allow_empty_selection = allow_empty_selection))
  else:
    raise Sorry('Ambiguous selection.')
  #
  def selection_info (idx) :
    sele_str = str(selection_strings[idx])
    if (parameter_name is not None) :
      return "for parameter %s (%s)" % (parameter_name, sele_str)
    else :
      return "(%s)" % sele_str
  if(len(selections)>1):
    if(not isinstance(selections[0], flex.bool)):
      tmp = flex.bool(xray_structure.scatterers().size(), selections[0]).as_int()
    else:
      tmp = selections[0].deep_copy().as_int()
    for k_, tmp_s in enumerate(selections[1:]):
      k = k_ + 1 # XXX Python 2.5 workaround
      if(not isinstance(tmp_s, flex.bool)):
        tmp = tmp + flex.bool(xray_structure.scatterers().size(),tmp_s).as_int()
      else:
        tmp = tmp + tmp_s.as_int()
    if(flex.max(tmp)>1):
      sel1, sel2 = find_overlapping_selections(selections, selection_strings)
      if (parameter_name is not None) :
        raise Sorry("One or more overlapping selections for %s:\n%s\n%s" %
          (parameter_name, sel1, sel2))
      else :
        raise Sorry("One or more overlapping selections:\n%s\n%s" %(sel1,sel2))
  #
  if(iselection):
    for i_seq, selection in enumerate(selections):
      if(hasattr(selection, "iselection")):
        selections[i_seq] = selections[i_seq].iselection()
  if(one_selection_array):
    s0 = selections[0]
    for s in selections[1:]:
      if(not iselection):
        s0 = s0 | s
      else:
        s0.extend(s)
    selections = s0
    if (iselection) :
      selections = selections.select(flex.sort_permutation(selections))
  return selections

def atom_selection(all_chain_proxies, string, allow_empty_selection = False):
  result = all_chain_proxies.selection(
    string=string,
    optional=(allow_empty_selection is not None))
  if (result is None):
    return None
  if (allow_empty_selection is not None):
    if (not allow_empty_selection and result.all_eq(False)):
      raise Sorry(
        "Selection string results in empty selection (selects no atoms): %s"
          % show_string(string))
  return result

def write_pdb_file(
      xray_structure,
      pdb_hierarchy,
      pdb_atoms = None,
      write_cryst1_record = True,
      selection = None,
      atoms_reset_serial = True,
      out = None,
      return_pdb_string = False):
  """
  Write the specified model to a PDB-format file, incorporating refined atomic
  parameters.
  """
  if (write_cryst1_record and out is not None):
    crystal_symmetry = xray_structure.crystal_symmetry()
    print >> out, pdb.format_cryst1_record(crystal_symmetry = crystal_symmetry)
    print >> out, pdb.format_scale_records(
      unit_cell = crystal_symmetry.unit_cell())
  # XXX PDB_TRANSITION SLOW
  xrs = xray_structure
  scatterers = xrs.scatterers()
  sites_cart = xrs.sites_cart()
  u_isos = xrs.extract_u_iso_or_u_equiv()
  if (selection is not None):
    pdb_hierarchy = pdb_hierarchy.select(selection)
    pdb_atoms = None
    scatterers = scatterers.select(selection)
    sites_cart = sites_cart.select(selection)
    u_isos = u_isos.select(selection)
  occupancies = scatterers.extract_occupancies()
  u_carts = scatterers.extract_u_cart_plus_u_iso(xrs.unit_cell())
  scat_types = scatterers.extract_scattering_types()
  if (pdb_atoms is None):
    pdb_atoms = pdb_hierarchy.atoms()
  # XXX PDB_TRANSITION SLOW
  for j_seq,atom in enumerate(pdb_atoms):
    atom.xyz = sites_cart[j_seq]
    atom.occ = occupancies[j_seq]
    atom.b = adptbx.u_as_b(u_isos[j_seq])
    if (scatterers[j_seq].flags.use_u_aniso()):
      atom.uij = u_carts[j_seq]
    else:
      atom.uij_erase()
    atom.set_element_and_charge_from_scattering_type_if_necessary(
      scattering_type=scat_types[j_seq])
  if (atoms_reset_serial):
    atoms_reset_serial_first_value = 1
  else:
    atoms_reset_serial_first_value = None
  if not return_pdb_string:
    out.write(pdb_hierarchy.as_pdb_string(
      append_end=True,
      atoms_reset_serial_first_value=atoms_reset_serial_first_value))
  else:
    return pdb_hierarchy.as_pdb_string(
      append_end=True,
      crystal_symmetry = xray_structure.crystal_symmetry(),
      atoms_reset_serial_first_value=atoms_reset_serial_first_value)

def print_programs_start_header(log, text):
  print >> log
  host_and_user().show(out= log)
  print >> log, date_and_time()
  print >> log
  print >> log, "-"*79
  print >> log, text
  print >> log, "-"*79
  print >> log

def set_log(args, out=sys.stdout, replace_stderr=True):
  log = multi_out()
  if(not "--quiet" in args):
     log.register(label="stdout", file_object=out)
  string_buffer = StringIO()
  string_buffer_plots = StringIO()
  log.register(label="log_buffer", file_object=string_buffer)
  if (replace_stderr) :
    sys.stderr = log
  return log

def print_header(line, out=None):
  str_utils.make_header(line, out=out)

def get_atom_selection(pdb_file_name, selection_string, iselection = False):
  processed_pdb_file = monomer_library.pdb_interpretation.process(
    mon_lib_srv = monomer_library.server.server(),
    ener_lib    = monomer_library.server.ener_lib(),
    file_name   = pdb_file_name,
    log         = None)
  xray_structure = processed_pdb_file.xray_structure(show_summary = False)
  result = get_atom_selections(
    all_chain_proxies = processed_pdb_file.all_chain_proxies,
    xray_structure    = xray_structure,
    selection_strings = [selection_string],
    iselection        = iselection)
  assert len(result) == 1
  return result[0]

cif_params = iotbx.phil.parse("""\
  file_name=None
    .optional=True
    .type=path
    .help=Monomer file(s) name (CIF)
    .multiple=True
    .short_caption=Restraints (CIF)
    .input_size = 400
    .style = bold input_file file_type:cif
""")

class process_pdb_file_srv(object):
  def __init__(self, crystal_symmetry          = None,
                     pdb_parameters            = None,
                     pdb_interpretation_params = None,
                     stop_for_unknowns         = None,
                     log                       = None,
                     cif_objects               = None,
                     cif_parameters            = None,
                     mon_lib_srv               = None,
                     ener_lib                  = None,
                     use_neutron_distances     = False):
    self.raw_records               = None
    self.crystal_symmetry          = crystal_symmetry
    self.pdb_parameters            = pdb_parameters
    self.pdb_interpretation_params = pdb_interpretation_params
    self.stop_for_unknowns         = stop_for_unknowns
    self.cif_objects               = cif_objects
    self.cif_parameters            = cif_parameters
    self.log                       = log
    self.use_neutron_distances     = use_neutron_distances
    if(mon_lib_srv is None): self.mon_lib_srv = monomer_library.server.server()
    else: self.mon_lib_srv = mon_lib_srv
    if(ener_lib is None):
      self.ener_lib = monomer_library.server.ener_lib(
        use_neutron_distances=use_neutron_distances,
        )
    else: self.ener_lib = ener_lib
    if(self.log is None): self.log = sys.stdout
    if(self.log == False): self.log = None

  def process_pdb_files(self, pdb_file_names = None, raw_records = None,
                        stop_if_duplicate_labels = True,
                        allow_missing_symmetry=False):
    assert [pdb_file_names, raw_records].count(None) == 1
    if(self.cif_objects is not None):
      self._process_monomer_cif_files()
    return self._process_pdb_file(
      pdb_file_names           = pdb_file_names,
      raw_records              = raw_records,
      stop_if_duplicate_labels = stop_if_duplicate_labels,
      allow_missing_symmetry            = allow_missing_symmetry)

  def _process_pdb_file(self, pdb_file_names, raw_records,
                        stop_if_duplicate_labels,
                        allow_missing_symmetry=False):
    if(raw_records is None):
      pdb_combined = combine_unique_pdb_files(file_names=pdb_file_names)
      pdb_combined.report_non_unique(out=self.log)
      if (len(pdb_combined.unique_file_names) == 0):
        raise Sorry("No coordinate file given.")
      if(self.pdb_parameters is not None):
        self.pdb_parameters.file_name = [os.path.abspath(file_name)
          for file_name in pdb_combined.unique_file_names]
      raw_records = pdb_combined.raw_records
    self.raw_records = raw_records
    try :
      pdb_inp = iotbx.pdb.input(source_info = None,
                                lines       = flex.std_string(raw_records))
      if(self.crystal_symmetry is None):
        self.crystal_symmetry = pdb_inp.crystal_symmetry()
    except ValueError, e :
      raise Sorry("PDB format error:\n%s" % str(e))
    if(pdb_inp.atoms().size() == 0):
      msg = ["No atomic coordinates found in PDB files:"]
      if(pdb_file_names is not None):
        for file_name in pdb_file_names:
          msg.append("  %s" % show_string(file_name))
      raise Sorry("\n".join(msg))
    if(stop_if_duplicate_labels):
      pdb_inp.construct_hierarchy(). \
        overall_counts().raise_duplicate_atom_labels_if_necessary()
    #
    # converge pdb_interpretation_params and use_neutron from scattering
    # table selection
    #
    restraints_loading_flags = \
      monomer_library.pdb_interpretation.get_restraints_loading_flags(
        self.pdb_interpretation_params)
    if self.use_neutron_distances:
      restraints_loading_flags["use_neutron_distances"] = self.use_neutron_distances
    if(not allow_missing_symmetry):
      if(self.crystal_symmetry is None or
         [self.crystal_symmetry.unit_cell(),
          self.crystal_symmetry.space_group()].count(None)>0):
        raise Sorry("Crystal symmetry is missing or cannot be extracted.")
    processed_pdb_file = monomer_library.pdb_interpretation.process(
      mon_lib_srv              = self.mon_lib_srv,
      ener_lib                 = self.ener_lib,
      params                   = self.pdb_interpretation_params,
      raw_records              = raw_records,
      strict_conflict_handling = False,
      crystal_symmetry         = self.crystal_symmetry,
      force_symmetry           = True,
      log                      = self.log,
      restraints_loading_flags = restraints_loading_flags,
      substitute_non_crystallographic_unit_cell_if_necessary=allow_missing_symmetry)
    processed_pdb_file.xray_structure(show_summary=True)
    if self.stop_for_unknowns == False:
      ignore_unknown_nonbonded_energy_types=True # only ignore if specified
    else:
      ignore_unknown_nonbonded_energy_types=False
    msg = processed_pdb_file.all_chain_proxies.fatal_problems_message(
      ignore_unknown_scattering_types=False,
      ignore_unknown_nonbonded_energy_types=ignore_unknown_nonbonded_energy_types)
    if (msg is not None):
      if (self.stop_for_unknowns is not None) :
        msg += """
  Alternatively, to continue despite this problem use:
    stop_for_unknowns=False"""
      raise Sorry(msg)
    if (self.log):
      print >> self.log
    return processed_pdb_file, pdb_inp

  def _process_monomer_cif_files(self):
    all = []
    index_dict = {}
    if(self.cif_parameters is not None):
      for file_name in self.cif_parameters.file_name:
        file_name = libtbx.path.canonical_path(file_name=file_name)
        index_dict[file_name] = len(all)
        all.append((file_name,None))
    for file_name,cif_object in self.cif_objects:
      file_name = libtbx.path.canonical_path(file_name=file_name)
      index_dict[file_name] = len(all)
      all.append((file_name,cif_object))
    unique_indices = index_dict.values()
    unique_indices.sort()
    unique = flex.select(sequence=all, permutation=unique_indices)
    if(self.cif_parameters is not None): del self.cif_parameters.file_name[:]
    for file_name,cif_object in unique:
      if(cif_object is None):
        self.mon_lib_srv.process_cif(file_name=file_name)
        self.ener_lib.process_cif(file_name=file_name)
      else:
        self.mon_lib_srv.process_cif_object(
          cif_object=cif_object, file_name=file_name)
        self.ener_lib.process_cif_object(cif_object=cif_object,
                                         file_name=file_name)
      if(self.cif_parameters is not None):
        self.cif_parameters.file_name.append(file_name)

def remove_selections(selection, other, size):
  other_as_1d = flex.size_t()
  if(isinstance(other, flex.size_t)):
    other_as_1d = other
  else:
    for o_ in other:
      for o__ in o_:
        if(not isinstance(o__,flex.size_t)):
          o__ = flex.size_t(o__)
        other_as_1d.extend(o__)
  if(len(other_as_1d) == 0): return selection
  other_as_1d_as_bool = flex.bool(size, flex.size_t(other_as_1d))
  result = []
  for s_ in selection:
    new_group = []
    for s__ in s_:
      new_group_member = []
      for s___ in s__:
        if(not other_as_1d_as_bool[s___]):
          new_group_member.append(s___)
      if(len(new_group_member) > 0):
        new_group.append(new_group_member)
    if(len(new_group) > 0):
      result.append(new_group)
  return result

def combine_hd_exchangable(hierarchy):
  result = []
  for model in hierarchy.models():
    for chain in model.chains():
      for residue_group in chain.residue_groups():
        for i_gr1, atom_group_1 in enumerate(residue_group.atom_groups()):
          for i_gr2, atom_group_2 in enumerate(residue_group.atom_groups()):
            if(atom_group_1.altloc != atom_group_2.altloc and i_gr2 > i_gr1):
              for atom1 in atom_group_1.atoms():
                e1 = atom1.element.strip()
                n1 = atom1.name.strip()[1:]
                for atom2 in atom_group_2.atoms():
                  e2 = atom2.element.strip()
                  n2 = atom2.name.strip()[1:]
                  if(e1 in ["H","D"] and e2 in ["H","D"] and e1 != e2 and
                     n1 == n2):
                    result.append([[int(atom1.i_seq)], [int(atom2.i_seq)]])
  return result

def assert_xray_structures_equal(
      x1,
      x2,
      selection = None,
      sites = True,
      adp = True,
      occupancies = True,
      elements = True,
      scattering_types = True,
      eps = 1.e-6):
  assert x1.scatterers().size() == x2.scatterers().size()
  cs1 = x1.crystal_symmetry()
  cs2 = x2.crystal_symmetry()
  assert [cs1, cs2].count(None) in [0,2]
  assert cs1.is_similar_symmetry(cs2)
  if(selection is not None):
    x1 = x1.select(selection)
    x2 = x2.select(selection)
  if(sites):
    assert approx_equal(x1.sites_frac(), x2.sites_frac(), eps)
  if(adp):
    assert approx_equal(x1.extract_u_iso_or_u_equiv(),
                        x2.extract_u_iso_or_u_equiv(), eps)
  if(occupancies):
    assert approx_equal(x1.scatterers().extract_occupancies(),
                        x2.scatterers().extract_occupancies(), eps)
  if(elements):
    sct1 = x1.scatterers().extract_scattering_types()
    sct2 = x2.scatterers().extract_scattering_types()
    for sct1_, sct2_ in zip(sct1, sct2):
      assert sct1_ == sct2_, [sct1_, sct2_]
  if(scattering_types):
    sr1 = x1.scattering_type_registry().unique_gaussians_as_list()
    sr2 = x2.scattering_type_registry().unique_gaussians_as_list()
    for s1,s2 in zip(sr1,sr2):
      assert approx_equal(s1.parameters(), s2.parameters(), eps)

def compare_hierarchy(hierarchy, scatterers, cell):
  from libtbx.test_utils import approx_equal
  # Primary "view" of hierarchy:
  #   model, chain, residue_group, atom_group, atom"""
  n = hierarchy.atoms_size()
  n2 = scatterers.size()
  assert n == n2, " size mismatch %d != %d"%(n,n2)
  match = flex.bool()
  match.resize(n, False)
  assert match.size() == n
  if n>0:
    assert match[0] == False
    assert match[n-1] == False
  for model in hierarchy.models():
    # print 'model: "%s"' % model.id
    for chain in model.chains():
      # print 'chain: "%s"' % chain.id
      for residue_group in chain.residue_groups():
        #print '  residue_group: resseq="%s" icode="%s"' % (
        #  residue_group.resseq, residue_group.icode)
        for atom_group in residue_group.atom_groups():
          #print '    atom_group: altloc="%s" resname="%s"' % (
          #  atom_group.altloc, atom_group.resname)
          for atom in atom_group.atoms():
            # print_atom(atom)
            assert atom.i_seq < n
            assert match[atom.i_seq] == False
            s = scatterers[atom.i_seq]
            # assert (atom.serial_as_int() == atom.i_seq + 1)
            match[atom.i_seq] = True
            aes=[atom.element.strip().upper(),s.element_symbol().strip().upper()]
            assert aes[0]==aes[1], aes
            if len(atom.name.strip())<1:
              raise RuntimeError(
                "\nAtom serial='%s' chain='%s' resseq='%s' resname='%s' " %
                (atom.serial,
                 chain.id,
                 residue_group.resseq,
                 atom_group.resname) +
                "has no atom name. \nPlease check your input model.")
            # XXX ADD CHARGE!
            # assert len(s.label.strip())>0
            # assert approx_equal(atom.occ, s.occupancy, 0.01)
            assert approx_equal(cell.orthogonalize(s.site), atom.xyz, 0.001)
            #assert approx_equal(atom.b, cctbx.adptbx.u_as_b(s.u_iso), 0.05)
  #
  assert match.all_eq(True)
  if n>0:
    assert match[0] == True
    assert match[n-1] == True

def assert_model_is_consistent(model):
  xs = model.xray_structure
  unit_cell = xs.unit_cell()
  scatterers = xs.scatterers()
  hier = model.pdb_hierarchy()
  compare_hierarchy(hier, scatterers, unit_cell)

def assert_water_is_consistent(model):
  xs = model.xray_structure
  unit_cell = xs.unit_cell()
  scatterers = xs.scatterers()
  hier = model.pdb_hierarchy()
  water_rgs = model.extract_water_residue_groups()
  for rg in water_rgs:
    if (rg.atom_groups_size() != 1):
      raise RuntimeError(
        "Not implemented: cannot handle water with alt. conf.")
    ag = rg.only_atom_group()
    atoms = ag.atoms()
    h_atoms = []
    o_atom=None
    if atoms.size()>0:
      for atom in atoms:
        if (atom.element.strip() == "O"):
          o_atom = atom
        else:
          h_atoms.append(atom)
    else:
      assert False
    o_i = o_atom.i_seq
    o_site = scatterers[o_i].site
    for hatom in h_atoms:
      hsite = scatterers[hatom.i_seq].site
      doh = unit_cell.distance(hsite, o_site)
      assert doh >0.35 and doh < 1.45, doh


class xray_structures_from_processed_pdb_file(object):

  def __init__(self, processed_pdb_file, scattering_table, d_min, log = None):
    self.xray_structures = []
    self.model_selections = []
    self.neutron_scattering_dict = None
    self.xray_scattering_dict = None
    self.xray_structure_all = \
     processed_pdb_file.xray_structure(show_summary = False)
    # XXX ad hoc manipulation
    for sc in self.xray_structure_all.scatterers():
      lbl=sc.label.split()
      if("IAS" in lbl and sc.scattering_type=="?" and lbl[1].startswith("IS")):
        sc.scattering_type = lbl[1]
    #
    if(self.xray_structure_all is None):
      raise Sorry("Cannot extract xray_structure.")
    if(self.xray_structure_all.scatterers().size()==0):
      raise Sorry("Empty xray_structure.")
    all_chain_proxies = processed_pdb_file.all_chain_proxies
    self.xray_scattering_dict, self.neutron_scattering_dict = \
      setup_scattering_dictionaries(
        scattering_table  = scattering_table,
        all_chain_proxies = all_chain_proxies,
        xray_structure    = self.xray_structure_all,
        d_min             = d_min,
        log               = log)
    model_indices = all_chain_proxies.pdb_inp.model_indices()
    if(len(model_indices)>1):
       model_indices_padded = flex.size_t([0])
       model_indices_padded.extend(model_indices)
       ranges = []
       for i, v in enumerate(model_indices_padded):
         try: ranges.append([model_indices_padded[i],
                             model_indices_padded[i+1]])
         except IndexError: pass
       for ran in ranges:
         sel = flex.size_t(range(ran[0],ran[1]))
         self.model_selections.append(sel)
         self.xray_structures.append(self.xray_structure_all.select(sel))
    else:
      self.model_selections.append(
        flex.size_t(xrange(self.xray_structure_all.scatterers().size())) )
      self.xray_structures.append(self.xray_structure_all)

def setup_scattering_dictionaries(scattering_table,
                                  xray_structure,
                                  d_min,
                                  log = None,
                                  all_chain_proxies = None):
  xray_scattering_dict, neutron_scattering_dict = [None,]*2
  if(log is not None):
    str_utils.make_header("Scattering factors", out = log)
  known_scattering_tables = [
    "n_gaussian", "wk1995", "it1992", "electron", "neutron"]
  if(not (scattering_table in known_scattering_tables)):
    raise Sorry("Unknown scattering_table: %s\n%s"%
      (show_string(scattering_table),
      "Possible choices are: %s"%" ".join(known_scattering_tables)))
  if(scattering_table in ["n_gaussian", "wk1995", "it1992", "electron"]):
    xray_structure.scattering_type_registry(
      table = scattering_table,
      d_min = d_min,
      types_without_a_scattering_contribution=["?"])
    import mmtbx.ias
    xray_structure.scattering_type_registry(
      custom_dict = mmtbx.ias.ias_scattering_dict)
    xray_scattering_dict = \
      xray_structure.scattering_type_registry().as_type_gaussian_dict()
    if(log is not None):
      print_statistics.make_sub_header("X-ray scattering dictionary",out=log)
      xray_structure.scattering_type_registry().show(out = log)
  if(scattering_table == "neutron"):
    try :
      neutron_scattering_dict = \
        xray_structure.switch_to_neutron_scattering_dictionary()
    except ValueError, e :
      raise Sorry("Error setting up neutron scattering dictionary: %s"%str(e))
    if(log is not None):
      print_statistics.make_sub_header(
        "Neutron scattering dictionary", out = log)
      xray_structure.scattering_type_registry().show(out = log)
    xray_structure.scattering_type_registry_params.table = "neutron"
  if(all_chain_proxies is not None):
    scattering_type_registry = all_chain_proxies.scattering_type_registry
    if(scattering_type_registry.n_unknown_type_symbols() > 0):
      scattering_type_registry.report(
        pdb_atoms = all_chain_proxies.pdb_atoms,
        log = log,
        prefix = "",
        max_lines = None)
      raise Sorry("Unknown scattering type symbols.\n"
        "  Possible ways of resolving this error:\n"
        "    - Edit columns 77-78 in the PDB file to define"
          " the scattering type.\n"
        "    - Provide custom monomer definitions for the affected residues.")
    if(log is not None):
      print >> log
  return xray_scattering_dict, neutron_scattering_dict

def fmodel_manager(
      f_obs,
      xray_structure                = None,
      r_free_flags                  = None,
      f_mask                        = None,
      f_calc                        = None,
      ignore_r_free_flags           = False,
      target_name                   = "ml",
      k_mask                        = None,
      k_anisotropic                 = None,
      hl_coeff                      = None,
      epsilons                      = None,
      use_f_model_scaled            = False,
      twin_law                      = None,
      detwin_mode                   = None,
      detwin_map_types              = None,
      alpha_beta_params             = None,
      sf_and_grads_accuracy_params  = mmtbx.f_model.sf_and_grads_accuracy_master_params.extract(),
      mask_params                   = None,
      max_number_of_resolution_bins = None,
      n_resolution_bins_output      = None):
  if(r_free_flags is None or ignore_r_free_flags):
    r_free_flags = f_obs.array(data = flex.bool(f_obs.data().size(), False))
  if(twin_law is None):
    fmodel = mmtbx.f_model.manager(
      alpha_beta_params            = alpha_beta_params,
      xray_structure               = xray_structure,
      sf_and_grads_accuracy_params = sf_and_grads_accuracy_params,
      use_f_model_scaled           = use_f_model_scaled,
      r_free_flags                 = r_free_flags,
      mask_params                  = mask_params,
      target_name                  = target_name,
      f_obs                        = f_obs,
      f_mask                       = f_mask,
      f_calc                       = f_calc,
      abcd                         = hl_coeff,
      epsilons                     = epsilons,
      max_number_of_bins           = max_number_of_resolution_bins,
      n_resolution_bins_output     = n_resolution_bins_output)
  else:
    from cctbx import sgtbx
    twin_law_xyz = sgtbx.rt_mx(symbol=twin_law, r_den=12, t_den=144)
    fmodel = twin_f_model.twin_model_manager(
      f_obs                        = f_obs,
      f_mask                       = f_mask,
      f_calc                       = f_calc,
      r_free_flags                 = r_free_flags,
      sf_and_grads_accuracy_params = sf_and_grads_accuracy_params,
      xray_structure               = xray_structure,
      twin_law                     = twin_law_xyz,
      twin_law_str                 = twin_law,
      mask_params                  = mask_params,
      detwin_mode                  = detwin_mode,
      map_types                    = detwin_map_types)
    fmodel.twin = twin_law
  return fmodel

def fmodel_simple(f_obs,
                  xray_structures,
                  scattering_table,
                  r_free_flags             = None,
                  target_name              = "ml",
                  bulk_solvent_and_scaling = True,
                  bss_params               = None,
                  mask_params              = None,
                  twin_laws                = None,
                  skip_twin_detection      = False,
                  twin_switch_tolerance    = 2.0,
                  outliers_rejection       = True,
                  bulk_solvent_correction  = True,
                  anisotropic_scaling      = True,
                  log                      = None):
  if(r_free_flags is None):
    r_free_flags = f_obs.customized_copy(
      data = flex.bool(f_obs.data().size(), False))
  assert f_obs.is_in_asu()
  assert r_free_flags.is_in_asu()
  assert f_obs.indices().all_eq(r_free_flags.indices())
  assert f_obs.sys_absent_flags().data().count(True)==0
  if(bss_params is None):
    bss_params = bss.master_params.extract()
  bss_params.bulk_solvent = bulk_solvent_correction
  bss_params.anisotropic_scaling = anisotropic_scaling
  def get_fmodel(f_obs, xrs, flags, mp, tl, bssf, bssp, ro, om):
    fmodel = fmodel_manager(
      xray_structure = xrs.deep_copy_scatterers(),
      f_obs          = f_obs.deep_copy(),
      r_free_flags   = flags.deep_copy(),
      target_name    = target_name,
      mask_params    = mp,
      twin_law       = tl)
    if(bssf):
      fmodel.update_all_scales(params = bssp, log = log, optimize_mask=om)
    return fmodel
  if((twin_laws is None or twin_laws==[None]) and not skip_twin_detection):
    twin_laws = twin_analyses.get_twin_laws(miller_array=f_obs)
  optimize_mask=False
  # DEBUG twin_laws=None
  if(len(xray_structures) == 1):
    if(twin_laws is None): twin_laws = [None]
    if(twin_laws.count(None)==0): twin_laws.append(None)
    fmodel = get_fmodel(f_obs=f_obs, xrs=xray_structures[0], flags=r_free_flags,
      mp=mask_params, tl=None, bssf=bulk_solvent_and_scaling, bssp=bss_params,
      ro = outliers_rejection,om=optimize_mask)
    r_work = fmodel.r_work()
    for twin_law in twin_laws:
      if(twin_law is not None):
        fmodel_ = get_fmodel(f_obs=f_obs, xrs=xray_structures[0],
          flags=r_free_flags, mp=mask_params, tl=twin_law,
          bssf=bulk_solvent_and_scaling, bssp=bss_params, ro = outliers_rejection,
          om=optimize_mask)
        r_work_ = fmodel_.r_work()
        fl = abs(r_work-r_work_)*100 > twin_switch_tolerance and r_work_<r_work
        if(fl):
          r_work = r_work_
          fmodel = fmodel_.deep_copy()
          fmodel.twin = twin_law
          twin_switch_tolerance = 0
  else:
    # XXX Automatic twin detection is not available for multi-model.
    f_model_data = None
    xrs_as_one_structure = xray_structures[0].deep_copy_scatterers()
    f_masks_data = []
    for i_seq, xray_structure in enumerate(xray_structures):
      fmodel = fmodel_manager(
        xray_structure = xray_structure,
        target_name    = target_name,
        f_obs          = f_obs.deep_copy(),
        r_free_flags   = r_free_flags.deep_copy(),
        mask_params    = mask_params,
        twin_law       = None) # XXX Automatic twin detection is not available for multi-model.
      if(i_seq != 0):
        xrs_as_one_structure = xrs_as_one_structure.concatenate(xray_structure)
      if(i_seq == 0):
        f_model_data = fmodel.f_calc().data()
        f_masks_data = []
        for f in fmodel.f_masks():
          f_masks_data.append(f.data())
      else:
        f_model_data += fmodel.f_calc().data()
        fmsks = fmodel.f_masks()
        assert len(f_masks_data) == len(fmsks)
        for ifmd in range(len(f_masks_data)):
          f_masks_data[ifmd] += fmsks[ifmd].data()
    fmodel_average = fmodel.f_obs().array(data = f_model_data)
    f_masks_data_average = []
    for f in f_masks_data:
      f_masks_data_average.append(fmodel.f_obs().array(data = f/len(xray_structures)))
    fmodel_result = fmodel_manager(
      f_obs        = f_obs.deep_copy(),
      r_free_flags = r_free_flags.deep_copy(),
      f_calc       = fmodel_average,
      target_name  = target_name,
      mask_params  = mask_params,
      f_mask       = f_masks_data_average,
      twin_law     = None)
    if 0:
      # XXX this makes test perfect when fobs are computed with pdbtools
      fmodel_result = fmodel_manager(
          xray_structure = xrs_as_one_structure,
          f_obs          = f_obs,
          r_free_flags   = r_free_flags,
          mask_params    = mask_params,
          twin_law       = None)
    if(bulk_solvent_and_scaling):
      fmodel_result.update_all_scales(remove_outliers = outliers_rejection)
    fmodel = fmodel_result
  if(bulk_solvent_and_scaling and not fmodel.twin): # "not fmodel.twin" for runtime only
    fmodel.update_all_scales(remove_outliers = outliers_rejection)
  return fmodel

class process_command_line_args(object):
  def __init__(self,
               args,
               cmd_cs=None,
               master_params=None,
               log=None,
               home_scope=None,
               absolute_angle_tolerance=1.e-2,
               absolute_length_tolerance=1.e-2,
               suppress_symmetry_related_errors=False):
    self.log = log
    self.absolute_angle_tolerance=absolute_angle_tolerance
    self.absolute_length_tolerance=absolute_length_tolerance
    self.pdb_file_names   = []
    self.cif_objects      = []
    self.cif_file_names   = []
    self.reflection_files = []
    self.reflection_file_names = []
    self.phil_file_names  = []
    self.params           = None
    self.crystal_symmetry = None
    self.cmd_cs = cmd_cs
    self.reflection_file_server = None
    self.ccp4_map = None
    self.ccp4_map_file_name = None
    crystal_symmetries = []
    if(master_params is not None):
      assert home_scope is None
      parameter_interpreter = master_params.command_line_argument_interpreter(
        home_scope = home_scope)
    parsed_params = []
    command_line_params = []
    for arg in args:
      arg_is_processed = False
      arg_file = arg
      is_parameter = False
      if(arg.count("=")==1):
        arg_file = arg[arg.index("=")+1:]
        is_parameter = True
      #
      if(self.cmd_cs is not None and self.cmd_cs.unit_cell() is not None):
        crystal_symmetries.append(["cmd_line", self.cmd_cs])
      #
      if(os.path.isfile(arg_file)):
        arg_added_to_crystal_symmetry=False
        # Get crystal symmetry
        cs = crystal_symmetry_from_any.extract_from(arg_file)
        if(cs is not None and cs.unit_cell() is not None):
          crystal_symmetries.append([arg_file, cs])
          arg_added_to_crystal_symmetry=True

        af = any_file(file_name = arg_file)
        #### NEW, no idea why this does not work.
        #if(af.file_type=="phil"):
        #  params = af.file_content.objects
        #  parsed_params.extend(params)
        #  self.phil_file_names.append(arg_file)
        #  print parsed_params, "'%s'"%params[0].name, "'%s'"%str(master_params.name), arg_file
        #  arg_is_processed = True
        #### OLD
        params = None
        try: params = iotbx.phil.parse(file_name=arg_file)
        except KeyboardInterrupt: raise
        except RuntimeError: pass
        else:
          if(len(params.objects) == 0):
            params = None
        if(params is not None):
          parsed_params.append(params)
          arg_is_processed = True
          self.phil_file_names.append(arg_file)
          #print parsed_params, "'%s'"%params.name, "'%s'"%str(master_params.name), arg_file
        elif(af.file_type=="pdb"): # which may be mmcif too!
          if(not is_parameter):
            self.pdb_file_names.append(arg_file)
            arg_is_processed = True
        elif(af.file_type=="ccp4_map"):
          self.ccp4_map = af.file_content
          self.ccp4_map_file_name = arg_file
          if not arg_added_to_crystal_symmetry:
            crystal_symmetries.append([arg_file, af.crystal_symmetry()])
          arg_is_processed = True
        elif(af.file_type=="hkl"):
          self.reflection_files.append(af.file_content)
          self.reflection_file_names.append(arg)
          arg_is_processed = True
        elif(af.file_type=="cif"):
          cif_object = af.file_object.model()
          if(len(cif_object) > 0):
            self.cif_objects.append((arg_file, cif_object))
            self.cif_file_names.append(os.path.abspath(arg_file))
            arg_is_processed = True
      if(master_params is not None and is_parameter):
        try:
          params = parameter_interpreter.process(arg = arg)
        except Sorry, e:
          if(not os.path.isfile(arg)):
            if("=" in arg): raise
            raise Sorry("File not found: %s" % show_string(arg))
          raise Sorry("Unknown file format: %s" % arg)
        else:
          command_line_params.append(params)
    if(master_params is not None):
      self.params, unused_definitions = master_params.fetch(
        sources=parsed_params+command_line_params,
        track_unused_definitions=True)
      if(len(unused_definitions)):
        print >> self.log, "Unused parameter definitions:"
        for obj_loc in unused_definitions:
          print >> self.log, " ", str(obj_loc)
        print >> self.log, "*"*79
        print >> self.log
        raise Sorry("Unused parameter definitions.")
    else:
      assert len(command_line_params) == 0
    # Crystal symmetry: validate and finalize consensus object
    if(len(crystal_symmetries)>1):
      assert self.crystal_symmetry is None # make sure it's undefined from start
      cs0, cs0_source = crystal_symmetries[0][1], crystal_symmetries[0][0]
      for cs in crystal_symmetries:
         is_similar_cs = cs0.is_similar_symmetry(cs[1],
           absolute_angle_tolerance=self.absolute_angle_tolerance,
           absolute_length_tolerance=self.absolute_angle_tolerance)
         if(not is_similar_cs and not suppress_symmetry_related_errors):
           print >> self.log, cs0_source, cs0.unit_cell(), cs0.space_group_info()
           print >> self.log, cs[0], cs[1].unit_cell(), cs[1].space_group_info()
           msg = "Crystal symmetry mismatch between different files."
           raise Sorry("%s\n"%(msg))
      self.crystal_symmetry = cs0
    elif(len(crystal_symmetries) == 1):
      self.crystal_symmetry = crystal_symmetries[0][1]

  def get_reflection_file_server (self) :
    if (self.reflection_file_server is None) :
      reflection_file_server = reflection_file_utils.reflection_file_server(
        crystal_symmetry=self.crystal_symmetry,
        force_symmetry=True,
        reflection_files=self.reflection_files,
        err=sys.stderr)
      self.reflection_file_server = reflection_file_server
    return self.reflection_file_server

class pdb_file(object):

  def __init__(self, pdb_file_names,
                     crystal_symmetry=None,
                     cif_objects=[],
                     log=None,
                     ignore_unknown_nonbonded_energy_types=False,
                     use_neutron_distances = False):
    if(log is None): log = sys.stdout
    self.processed_pdb_files_srv = None
    self.processed_pdb_file = None
    self.cif_objects = cif_objects
    self.crystal_symmetry = crystal_symmetry
    self.pdb_file_names = pdb_file_names
    self.use_neutron_distances = use_neutron_distances
    self.ignore_unknown_nonbonded_energy_types = \
      ignore_unknown_nonbonded_energy_types
    pdb_combined = combine_unique_pdb_files(file_names = pdb_file_names)
    pdb_combined.report_non_unique(out = log)
    if(len(pdb_combined.unique_file_names) == 0):
      raise Sorry("No coordinate file given.")
    self.pdb_raw_records = pdb_combined.raw_records
    self.pdb_inp = iotbx.pdb.input(source_info = None,
      lines = flex.std_string(self.pdb_raw_records),
      raise_sorry_if_format_error=True)
    if(crystal_symmetry is not None and crystal_symmetry.unit_cell() is not None):
      self.pdb_inp.crystal_symmetry(crystal_symmetry = crystal_symmetry)

  def set_ppf(self, stop_if_duplicate_labels=True, log=None):
    if(log is None): log = StringIO()
    pdb_ip = mmtbx.monomer_library.pdb_interpretation.master_params.extract()
    pdb_ip.clash_guard.nonbonded_distance_threshold = -1.0
    pdb_ip.clash_guard.max_number_of_distances_below_threshold = 100000000
    pdb_ip.clash_guard.max_fraction_of_distances_below_threshold = 1.0
    pdb_ip.proceed_with_excessive_length_bonds=True
    self.processed_pdb_files_srv = process_pdb_file_srv(
      cif_objects               = self.cif_objects,
      pdb_interpretation_params = pdb_ip,
      crystal_symmetry          = self.crystal_symmetry,
      use_neutron_distances     = self.use_neutron_distances,
      stop_for_unknowns         = False,
      log                       = log)
    self.processed_pdb_file, self.pdb_inp = \
      self.processed_pdb_files_srv.process_pdb_files(raw_records =
        self.pdb_raw_records, stop_if_duplicate_labels = stop_if_duplicate_labels)
    msg = self.processed_pdb_file.all_chain_proxies.fatal_problems_message(
      ignore_unknown_scattering_types=False,
      ignore_unknown_nonbonded_energy_types=
        self.ignore_unknown_nonbonded_energy_types)
    if(msg is not None): raise Sorry(msg)

def model_simple(pdb_file_names,
                 log = None,
                 normalization = True,
                 cif_objects = [],
                 crystal_symmetry = None,
                 plain_pairs_radius = 5,
                 refinement_flags = None,
                 scattering_table = None,
                 d_min = None):
  cryst1 = None
  mmtbx_pdb_file = pdb_file(
    pdb_file_names   = pdb_file_names,
    cif_objects      = cif_objects,
    crystal_symmetry = crystal_symmetry,
    log              = log)
  mmtbx_pdb_file.set_ppf()
  xsfppf = mmtbx.utils.xray_structures_from_processed_pdb_file(
    processed_pdb_file = mmtbx_pdb_file.processed_pdb_file,
    scattering_table   = scattering_table,
    d_min              = d_min)
  if(len(xsfppf.xray_structures) > 1):
    raise Sorry("Multiple models not supported.")
  xray_structure = xsfppf.xray_structures[0]
  # XXX dirty
  class rf:
    def __init__(self, size):
      self.individual_sites=True
      self.individual_adp = False
      self.sites_individual = flex.bool(size, True)
      self.sites_torsion_angles = None
  if(refinement_flags is None):
    refinement_flags = rf(size = xray_structure.scatterers().size())
  #
  sctr_keys=xray_structure.scattering_type_registry().type_count_dict().keys()
  has_hd = "H" in sctr_keys or "D" in sctr_keys
  geometry = mmtbx_pdb_file.processed_pdb_file.geometry_restraints_manager(
    show_energies                = False,
    plain_pairs_radius           = plain_pairs_radius,
    assume_hydrogens_all_missing = not has_hd)
  restraints_manager = mmtbx.restraints.manager(
    geometry      = geometry,
    normalization = normalization)
  pdb_hierarchy = \
    mmtbx_pdb_file.processed_pdb_file.all_chain_proxies.pdb_hierarchy
  from mmtbx import model
  result = model.manager(
    processed_pdb_files_srv = mmtbx_pdb_file.processed_pdb_files_srv,
    restraints_manager      = restraints_manager,
    xray_structure          = xray_structure,
    refinement_flags        = refinement_flags,
    pdb_hierarchy           = pdb_hierarchy,
    log                     = log)
  return result

def extract_tls_and_u_total_from_pdb(
      f_obs,
      r_free_flags,
      xray_structure,
      tls_selections,
      tls_groups):
  xrs_1 = xray_structure.deep_copy_scatterers()
  xrs_2 = xray_structure.deep_copy_scatterers()
  mmtbx.tls.tools.combine_tls_and_u_local(xray_structure = xrs_2,
    tls_selections = tls_selections, tls_groups = tls_groups)
  #
  selection = flex.random_bool(size=f_obs.data().size(),
    threshold=500./f_obs.data().size())
  f_obs = f_obs.select(selection)
  r_free_flags = r_free_flags.select(selection)
  bss_params = bss.master_params.extract()
  bss_params.k_sol_b_sol_grid_search=False
  bss_params.number_of_macro_cycles=1
  r_work = 999.
  i_best = None
  for i, xrs in enumerate([xrs_1, xrs_2]):
    fmodel = mmtbx.f_model.manager(xray_structure = xrs,
                                   f_obs          = f_obs,
                                   r_free_flags   = r_free_flags,
                                   target_name    = "ls_wunit_k1")
    fmodel.update_all_scales(params = bss_params)
    r_work_ = fmodel.r_work()
    if(r_work_ < r_work):
      r_work = r_work_
      i_best = i
  if(i_best == 0): result = xrs_1
  else: result = xrs_2
  return result


class guess_observation_type(object):

  data_size = 500

  def __init__(self, f_obs, label, xray_structure, r_free_flags=None):
    self.f_obs_original = f_obs.deep_copy()
    self.label = label
    self.r_free_flags_original = None
    if(r_free_flags is not None):
      self.r_free_flags_original = r_free_flags.deep_copy()
      r_free_flags = r_free_flags.map_to_asu().remove_systematic_absences()
    f_obs = f_obs.map_to_asu().remove_systematic_absences()
    f_obs = f_obs.set_observation_type(observation_type = None)
    #
    sigmas = f_obs.sigmas()
    if(sigmas is not None and abs(flex.max(sigmas)-flex.min(sigmas)) > 1.e-3
       and sigmas.size() >= self.data_size):
      for sig_cut in [3.0,2.0,1.0,0.0]:
        f_obs_ = f_obs.sigma_filter(cutoff_factor = sig_cut)
        if(f_obs_.data().size() >= self.data_size): break
      if(f_obs_.size() >= self.data_size): f_obs = f_obs_.deep_copy()
    #
    d_max, d_min = f_obs.d_max_min()
    if(d_min<=0.25):
      f_obs = f_obs.resolution_filter(d_min = 0.25)
      if(r_free_flags is not None):
        r_free_flags = r_free_flags.resolution_filter(d_min = 0.25)
    if(d_min < 1.5): d_min = 1.5
    if(d_max > 6.0 and d_max-d_min > 1.0): d_max = 6.0
    f_obs_ = f_obs.resolution_filter(d_min = d_min, d_max = d_max)
    if(f_obs_.size() >= self.data_size): f_obs = f_obs_
    #
    results = []
    for dtype in ["X","N"]:
      xrs = xray_structure.deep_copy_scatterers()
      err = None
      if(dtype=="N"):
        try:
          xrs.switch_to_neutron_scattering_dictionary()
        except Exception, e:
          err = str(e)
      if(err is None):
        f_calc = f_obs.structure_factors_from_scatterers(
          xray_structure = xrs).f_calc()
        for ftype in ["F","FFORCE","IFORCE"]:
          f = f_obs.deep_copy()
          if(ftype=="FFORCE"):
            f = f_obs.f_sq_as_f()
          elif(ftype=="IFORCE"):
            f = f_obs.f_as_f_sq()
          f.set_observation_type_xray_amplitude()
          scattering_table = "wk1995"
          if(dtype=="N"): scattering_table="neutron"
          fmodel = self.get_r_factor(
            f_obs               = f.deep_copy(),
            f_calc              = f_calc.deep_copy(),
            scattering_table    = scattering_table,
            xray_structure      = xrs.deep_copy_scatterers(),
            twin_switch_tolerance = 5.,
            skip_twin_detection = True)
          results.append([dtype,ftype,fmodel.twin,fmodel.r_work()])
      else:
        results.append([dtype,ftype,False,1.e9])
        results.append([dtype,ftype,False,1.e9])
        results.append([dtype,ftype,False,1.e9])
    #
    print "All scores (stage 1):"
    for r in results:
      st_r = " ".join(["%6s"%str(r_) for r_ in r])
      print st_r
    #
    results_x = []
    results_n = []
    for r in results:
      if(r[0]=="X"): results_x.append(r)
      elif(r[0]=="N"): results_n.append(r)
      else: raise RuntimeError
    #
    result_best_x, rbx = self.find_best(results = results_x)
    result_best_n, rbn = self.find_best(results = results_n)
    if(rbx > rbn and abs(rbx - rbn)*100. > 8.):
      if(result_best_n is not None):
        self.result = result_best_n
      else:
        self.result = ["N", self.label, None, None]
    else:
      if(result_best_x is not None):
        self.result = result_best_x
      else:
        self.result = ["X", self.label, None, None]
    if(len(self.result)==0): print "Answer: %s"%self.label
    elif([self.result[2], self.result[3]].count(None)==2):
      print "Answer: %s_%s"%(self.result[1], self.result[0])
    else:
      print "Answer: %s"%" ".join(["%6s"%str(r_) for r_ in self.result])

  def find_best(self, results):
    r_best = 1.e+9
    answer = None
    for r in results:
      if(abs(r[3]) < abs(r_best)):
        r_best = abs(r[3])
        answer = r[:]
    d0 = abs(results[0][3])
    d1 = abs(results[1][3])
    d2 = abs(results[2][3])
    diff = min(min(abs(d0-d1), abs(d0-d2)), abs(d1-d2))*100.
    if(diff < 4.0): answer = None
    #if(answer is not None):
    #  print "Answer: %s"%" ".join(["%6s"%str(r_) for r_ in answer])
    return answer, r_best

  def mtz_object(self):
    if(len(self.result)==0):
      label = self.label
    elif([self.result[2], self.result[3]].count(None)==2):
      label = self.label + "_" + self.result[0]
    else:
      r = self.result
      label = "OBS_%s"%r[0]
      if(r[1]=="F"):
        self.f_obs_original.set_observation_type_xray_amplitude()
        label = "F"+label
      elif(r[1]=="FFORCE"):
        self.f_obs_original.set_observation_type_xray_intensity()
        label = "I"+label
      elif(r[1]=="IFORCE"):
        self.f_obs_original = self.f_obs_original.f_as_f_sq()
        self.f_obs_original.set_observation_type_xray_amplitude()
        label = "F"+label
    mtz_dataset = self.f_obs_original.as_mtz_dataset(column_root_label = label)
    if(self.r_free_flags_original is not None):
      mtz_dataset.add_miller_array(
        miller_array      = self.r_free_flags_original,
        column_root_label = "R-free-flags")
    return mtz_dataset.mtz_object()

  def get_r_factor(self, f_obs, f_calc, scattering_table, xray_structure,
        twin_switch_tolerance, skip_twin_detection):
    r_free_flags = f_obs.array(data = flex.bool(f_obs.data().size(), False))
    for trial in xrange(3):
      result = outlier_rejection.outlier_manager(
        miller_obs   = f_obs,
        r_free_flags = r_free_flags,
        out          = "silent")
      s1 = result.basic_wilson_outliers().data()
      s2 = result.extreme_wilson_outliers().data()
      s3 = result.beamstop_shadow_outliers().data()
      s4 = result.model_based_outliers(f_model = f_calc).data()
      sel_out = s1 & s2 & s3 & s4
      f_obs = f_obs.select(sel_out)
      f_calc = f_calc.select(sel_out)
      r_free_flags = r_free_flags.select(sel_out)
    twin_laws = None
    if(not skip_twin_detection):
      twin_laws = twin_analyses.get_twin_laws(miller_array=f_obs)
      twin_laws.append(None)
    params = bss.master_params.extract()
    params.k_sol_grid_search_min = 0.0
    params.k_sol_grid_search_max = 0.35
    params.k_sol_step = 0.35
    params.b_sol_grid_search_min = 0.0
    params.b_sol_grid_search_max = 91.
    params.b_sol_step = 45.
    params.target = "ls_wunit_k1"
    fmodel = fmodel_simple(
      f_obs                    = f_obs,
      scattering_table         = scattering_table,
      xray_structures          = [xray_structure],
      r_free_flags             = r_free_flags,
      target_name              = "ls_wunit_k1",
      bulk_solvent_and_scaling = True,
      bss_params               = params,
      twin_switch_tolerance    = twin_switch_tolerance,
      skip_twin_detection      = skip_twin_detection,
      twin_laws                = twin_laws)
    return fmodel

class fmodel_from_xray_structure(object):

  def __init__(self, xray_structure,
                     f_obs = None,
                     params = None,
                     r_free_flags_fraction = None,
                     add_sigmas = False,
                     twin_law = None,
                     twin_fraction = None,
                     target = "ml",
                     out = None,
                     merge_r_free_flags = None):
    if(out is None): out = sys.stdout
    self.add_sigmas = add_sigmas
    if(params is None):
      params = mmtbx.command_line.fmodel.\
        fmodel_from_xray_structure_master_params.extract()
    if(r_free_flags_fraction is None):
      if(params.r_free_flags_fraction is not None):
        r_free_flags_fraction = params.r_free_flags_fraction
      else:
        r_free_flags_fraction = 0.1
    if(f_obs is None):
      hr = None
      try: hr = params.high_resolution
      except Exception: self.Sorry_high_resolution_is_not_defined()
      if(params.scattering_table == "neutron"):
        xray_structure.switch_to_neutron_scattering_dictionary()
      else:
        xray_structure.scattering_type_registry(
          table = params.scattering_table, d_min = hr)
      if(hr is None): self.Sorry_high_resolution_is_not_defined()
      f_obs = xray_structure.structure_factors(d_min = hr).f_calc()
      sfga = params.structure_factors_accuracy
      f_obs = f_obs.structure_factors_from_scatterers(
         xray_structure = xray_structure,
         algorithm                    = sfga.algorithm,
         cos_sin_table                = sfga.cos_sin_table,
         grid_resolution_factor       = sfga.grid_resolution_factor,
         quality_factor               = sfga.quality_factor,
         u_base                       = sfga.u_base,
         b_base                       = sfga.b_base,
         wing_cutoff                  = sfga.wing_cutoff,
         exp_table_one_over_step_size = sfga.exp_table_one_over_step_size
         ).f_calc()
      lr = None
      try: lr = params.low_resolution
      except Exception: RuntimeError("Parameter scope does not have 'low_resolution'.")
      if(params.low_resolution is not None):
        f_obs = f_obs.resolution_filter(d_max = lr)
    else:
      assert (f_obs.crystal_symmetry() is not None)
      assert (f_obs.unit_cell() is not None) and (f_obs.space_group() is not None)
      try: hr = params.high_resolution
      except Exception: hr = None
      try: lr = params.low_resolution
      except Exception: lr = None
      f_obs = f_obs.resolution_filter(d_max = lr, d_min = hr)
      if(params.scattering_table == "neutron"):
        xray_structure.switch_to_neutron_scattering_dictionary()
      else:
        xray_structure.scattering_type_registry(
          table = params.scattering_table, d_min = f_obs.d_min())
    r_free_flags = f_obs.generate_r_free_flags(fraction = r_free_flags_fraction,
      use_lattice_symmetry=False)
    fmodel = mmtbx.f_model.manager(
      xray_structure               = xray_structure,
      sf_and_grads_accuracy_params = params.structure_factors_accuracy,
      r_free_flags                 = r_free_flags,
      mask_params                  = params.mask,
      target_name                  = target,
      twin_law                     = twin_law,
      twin_fraction                = twin_fraction,
      f_obs                        = abs(f_obs),
      b_cart                       = params.fmodel.b_cart,
      k_sol                        = params.fmodel.k_sol,
      b_sol                        = params.fmodel.b_sol)
    f_model = fmodel.f_model()
    f_model = f_model.array(data = f_model.data()*params.fmodel.scale)
    try:
      if(params.output.type == "real"):
        f_model = abs(f_model)
        f_model.set_observation_type_xray_amplitude()
        if(params.add_random_error_to_amplitudes_percent is not None):
          if(params.add_random_error_to_amplitudes_percent > 0):
            data = f_model.data()
            fr = f_model.data()*params.add_random_error_to_amplitudes_percent/100.
            ri = flex.double()
            for trial in xrange(data.size()):
              r = random.randint(0,1)
              if(r == 0): r = -1
              ri.append(r)
            data = data + ri*fr
            f_model = f_model.array(data=data)
    except AttributeError: pass
    except Exception: raise RuntimeError
    self.f_model = f_model
    self.params = params
    self.fmodel = fmodel
    self.r_free_flags = None
    if(self.add_sigmas):
      sigmas = flex.double(self.f_model.data().size(),1)
      self.f_model._sigmas = sigmas
    if(params.r_free_flags_fraction is not None):
      self.r_free_flags = fmodel.r_free_flags()
      if merge_r_free_flags and self.r_free_flags.anomalous_flag() :
        self.r_free_flags = self.r_free_flags.average_bijvoet_mates()

  def Sorry_high_resolution_is_not_defined(self):
    raise Sorry("High resolution limit is not defined. "\
      "Use 'high_resolution' keyword to define it.")

  def write_to_file(self, file_name, obs_type="amplitudes"):
    assert self.params.output.format in ["mtz", "cns"]
    assert file_name is not None
    op = self.params.output
    if(self.params.output.format == "cns"):
      ofo = open(file_name, "w")
      crystal_symmetry_as_cns_comments(
        crystal_symmetry=self.f_model, out=ofo)
      print >> ofo, "NREFlections=%d" % self.f_model.indices().size()
      print >> ofo, "ANOMalous=%s" % {0: "FALSE"}.get(
        int(self.f_model.anomalous_flag()), "TRUE")
      for n_t in [("%s"%op.label, "%s"%op.type.upper())]:
        print >> ofo, "DECLare NAME=%s DOMAin=RECIprocal TYPE=%s END"%n_t
      if(self.params.r_free_flags_fraction is not None):
        print >> ofo, "DECLare NAME=TEST DOMAin=RECIprocal TYPE=INTeger END"
      if(op.type == "complex"):
        arrays = [
          self.f_model.indices(), flex.abs(self.f_model.data()),
          self.f_model.phases(deg=True).data()]
        if(self.params.r_free_flags_fraction is not None):
          arrays.append(self.r_free_flags.data())
        for values in zip(*arrays):
          if(self.params.r_free_flags_fraction is None):
            print >> ofo, "INDE %d %d %d" % values[0],
            print >> ofo, " %s= %.6g %.6g" % (op.label, values[1],values[2])
          else:
            print >> ofo, "INDE %d %d %d" % values[0],
            print >> ofo, " %s= %.6g %.6g TEST=%d" % (op.label, values[1],
              values[2], values[3])
      else:
        arrays = [
          self.f_model.indices(), self.f_model.data()]
        if(self.params.r_free_flags_fraction is not None):
          arrays.append(self.r_free_flags.data())
        for values in zip(*arrays):
          if(self.params.r_free_flags_fraction is None):
            print >> ofo, "INDE %d %d %d" % values[0],
            print >> ofo, " %s= %.6g" % (op.label, values[1])
          else:
            print >> ofo, "INDE %d %d %d" % values[0],
            print >> ofo, " %s= %.6g TEST=%d" % (op.label, values[1],values[2])
    else:
      output_array = self.f_model
      if (obs_type == "intensities") :
        output_array = output_array.f_as_f_sq()
        output_array.set_observation_type_xray_intensity()
      mtz_dataset= output_array.as_mtz_dataset(column_root_label="%s"%op.label)
      if(self.params.r_free_flags_fraction is not None):
        mtz_dataset.add_miller_array(
          miller_array      = self.r_free_flags,
          column_root_label = "R-free-flags")
      mtz_object = mtz_dataset.mtz_object()
      mtz_object.write(file_name = file_name)

def rms_b_iso_or_b_equiv_bonded(restraints_manager, xray_structure,
                                ias_selection = None):
  result = None
  if(restraints_manager is not None):
    xrs_sel = xray_structure
    if(ias_selection is not None):
      xrs_sel = xray_structure.select(selection = ~ias_selection)
    bond_proxies_simple, asu = restraints_manager.geometry.\
        get_covalent_bond_proxies(sites_cart=xrs_sel.sites_cart())
    u_isos = xrs_sel.extract_u_iso_or_u_equiv()
    scatterers = xrs_sel.scatterers()
    values = flex.double()
    for proxy in bond_proxies_simple:
      i_seq, j_seq = proxy.i_seqs
      if(scatterers[i_seq].element_symbol() not in ["H", "D"] and
         scatterers[j_seq].element_symbol() not in ["H", "D"]):
        b_iso_i = adptbx.u_as_b(u_isos[i_seq])
        b_iso_j = adptbx.u_as_b(u_isos[j_seq])
        abs_diff_sq = abs(b_iso_i-b_iso_j)**2
        values.append(abs_diff_sq)
    if(values.size() == 0): return 0
    result = math.sqrt(flex.sum(values) / values.size())
  return result


def _get_selections_around_residue(
    n_atoms,
    xrs,
    res,
    special_position_settings,
    radius,):
  sel = flex.size_t()
  sel_res_mc = flex.size_t()
  for a in res.atoms():
    sel.append(a.i_seq)
    if a.name.strip() in ["N", "CA", "C", "O"]:
      sel_res_mc.append(a.i_seq)
  bsel = flex.bool(n_atoms, False)
  bsel.set_selected(sel, True)
  selection_around_residue = special_position_settings.pair_generator(
      sites_cart      = xrs.sites_cart(),
      distance_cutoff = radius
        ).neighbors_of(primary_selection = bsel)
  bsel_around_no_mc = selection_around_residue.deep_copy()
  bsel_around_no_mc.set_selected(sel_res_mc, False)
  return sel, bsel_around_no_mc, selection_around_residue

def _get_rotamers_evaluated(
    pdb_hierarchy,
    sel,
    xrs,
    crystal_gridding,
    bsel_around_no_mc,
    hd_sel,
    res,
    grm,
    reind_dict,
    mon_lib_srv,
    map_data=None,
    prefix="a"):
  from mmtbx.command_line import lockit
  from cctbx.geometry_restraints import nonbonded_overlaps as nbo
  assert xrs.scatterers().size() == pdb_hierarchy.atoms_size()
  rotamer_iterator = lockit.get_rotamer_iterator(
      mon_lib_srv         = mon_lib_srv,
      residue             = res,
      atom_selection_bool = None)
  if rotamer_iterator is None:
    return None
  i = 0
  inf = []
  sites_for_nb_overlaps = pdb_hierarchy.atoms().extract_xyz().deep_copy()
  for rotamer, rotamer_sites_cart in rotamer_iterator:
    assert rotamer_sites_cart.size() == res.atoms().size()
    for j, i_seq in enumerate(sel):
      sites_for_nb_overlaps[reind_dict[i_seq]] = rotamer_sites_cart[j]
    nb_overlaps = nbo.info(
        geometry_restraints_manager=grm,
        macro_molecule_selection=bsel_around_no_mc,
        sites_cart=sites_for_nb_overlaps,
        site_labels=None,
        hd_sel=hd_sel,
        do_only_macro_molecule=True,
        check_for_unknown_pairs=False)
    overlap_proxies = nb_overlaps.result.nb_overlaps_proxies_macro_molecule
    summ = 0
    for p in overlap_proxies:
      d = list(p)
      summ += d[3]-d[4]
    map_cc = 0
    # print "Map_data", map_data
    rsr_target = 0
    if map_data is not None:
      # STOP()
      # getting map score for rotamer
      rsr_target = maptbx.real_space_target_simple(
          unit_cell   = xrs.crystal_symmetry().unit_cell(),
          density_map = map_data,
          sites_cart  = sites_for_nb_overlaps)
      map_cc = 0

      # new_xrs = xrs.deep_copy_scatterers()
      # new_xrs.set_sites_cart(sites_for_nb_overlaps)
      # f_model = new_xrs.structure_factors(d_min=3).f_calc()
      # fft_map2 = miller.fft_map(
      #   crystal_gridding     = crystal_gridding,
      #   fourier_coefficients = f_model)
      # fft_map2.apply_sigma_scaling()
      # map_data2 = fft_map2.real_map_unpadded(in_place=False)
      # map_cc = flex.linear_correlation(
      #   x=map_data.as_1d(),
      #   y=map_data2.as_1d()).coefficient()
      print "rsr target, cc:", rsr_target, map_cc

    inf.append((i,
        rotamer.id,
        rotamer.frequency,
        nb_overlaps.result.nb_overlaps_macro_molecule,
        nb_overlaps.result.normalized_nbo_macro_molecule,
        summ,
        map_cc,
        rsr_target,
        rotamer_sites_cart))
    i += 1
  return sorted(inf, key=lambda x: (x[5], x[2])  )

def _find_theta(ap1, ap2, cur_xyz, needed_xyz):
  from mmtbx.building.loop_closure.ccd import ccd_python
  f, s_home, r_norm, r_home = ccd_python._get_f_r_s(
      axis_point_1=ap1,
      axis_point_2=ap2,
      moving_coor=cur_xyz,
      fixed_coor=needed_xyz)
  b = list(2*r_norm*(f.dot(r_home)))[0]
  c = list(2*r_norm*(f.dot(s_home)))[0]
  znam = math.sqrt(b*b+c*c)
  sin_alpha = c/znam
  cos_alpha = b/znam
  alpha = math.atan2(sin_alpha, cos_alpha)
  return math.degrees(alpha)

def backrub_move(
    prev_res,
    cur_res,
    next_res,
    angle,
    move_oxygens=False,
    accept_worse_rama=False,
    rotamer_manager=None,
    rama_manager=None):
  import boost.python
  ext = boost.python.import_ext("mmtbx_validation_ramachandran_ext")
  from mmtbx_validation_ramachandran_ext import rama_eval
  from scitbx.matrix import rotate_point_around_axis
  from mmtbx.conformation_dependent_library.multi_residue_class import ThreeProteinResidues, \
      RestraintsRegistry

  if abs(angle) < 1e-4:
    return
  if prev_res is None or next_res is None:
    return
  saved_res = [{},{},{}]
  for i, r in enumerate([prev_res, cur_res, next_res]):
    for a in r.atoms():
      saved_res[i][a.name.strip()] = a.xyz
  if rotamer_manager is None:
    rotamer_manager = RotamerEval()
  prev_ca = prev_res.find_atom_by(name=" CA ")
  cur_ca = cur_res.find_atom_by(name=" CA ")
  next_ca = next_res.find_atom_by(name=" CA ")
  if prev_ca is None or next_ca is None or cur_ca is None:
    return
  atoms_to_move = []
  atoms_to_move.append(prev_res.find_atom_by(name=" C  "))
  atoms_to_move.append(prev_res.find_atom_by(name=" O  "))
  for atom in cur_res.atoms():
    atoms_to_move.append(atom)
  atoms_to_move.append(next_res.find_atom_by(name=" N  "))
  for atom in atoms_to_move:
    assert atom is not None
    new_xyz = rotate_point_around_axis(
        axis_point_1 = prev_ca.xyz,
        axis_point_2 = next_ca.xyz,
        point        = atom.xyz,
        angle        = angle,
        deg          = True)
    atom.xyz = new_xyz
  if move_oxygens:
    registry = RestraintsRegistry()
    if rama_manager is None:
      rama_manager = rama_eval()
    tpr = ThreeProteinResidues(geometry=None, registry=registry)
    tpr.append(prev_res)
    tpr.append(cur_res)
    tpr.append(next_res)
    phi_psi_angles = tpr.get_phi_psi_angles()
    rama_key = tpr.get_ramalyze_key()
    ev_before = rama_manager.evaluate_angles(rama_key, phi_psi_angles[0], phi_psi_angles[1])
    theta1 = _find_theta(
        ap1 = prev_ca.xyz,
        ap2 = cur_ca.xyz,
        cur_xyz = prev_res.find_atom_by(name=" O  ").xyz,
        needed_xyz = saved_res[0]["O"])
    theta2 = _find_theta(
        ap1 = cur_ca.xyz,
        ap2 = next_ca.xyz,
        cur_xyz = cur_res.find_atom_by(name=" O  ").xyz,
        needed_xyz = saved_res[1]["O"])
    for a in [prev_res.find_atom_by(name=" C  "),
        prev_res.find_atom_by(name=" O  "),
        cur_res.find_atom_by(name=" C  ")]:
      new_xyz = rotate_point_around_axis(
              axis_point_1 = prev_ca.xyz,
              axis_point_2 = cur_ca.xyz,
              point        = a.xyz,
              angle        = theta1,
              deg          = True)
      a.xyz = new_xyz
    for a in [cur_res.find_atom_by(name=" C  "),
        cur_res.find_atom_by(name=" O  "),
        next_res.find_atom_by(name=" N  ")]:
      new_xyz = rotate_point_around_axis(
              axis_point_1 = cur_ca.xyz,
              axis_point_2 = next_ca.xyz,
              point        = a.xyz,
              angle        = theta2,
              deg          = True)
      a.xyz = new_xyz
    phi_psi_angles = tpr.get_phi_psi_angles()
    rama_key = tpr.get_ramalyze_key()
    ev_after = rama_manager.evaluate_angles(rama_key, phi_psi_angles[0], phi_psi_angles[1])
    if ev_before > ev_after and not accept_worse_rama:
      for a in [prev_res.find_atom_by(name=" C  "),
          prev_res.find_atom_by(name=" O  "),
          cur_res.find_atom_by(name=" C  ")]:
        new_xyz = rotate_point_around_axis(
                axis_point_1 = prev_ca.xyz,
                axis_point_2 = cur_ca.xyz,
                point        = a.xyz,
                angle        = -theta1,
                deg          = True)
        a.xyz = new_xyz
      for a in [cur_res.find_atom_by(name=" C  "),
          cur_res.find_atom_by(name=" O  "),
          next_res.find_atom_by(name=" N  ")]:
        new_xyz = rotate_point_around_axis(
                axis_point_1 = cur_ca.xyz,
                axis_point_2 = next_ca.xyz,
                point        = a.xyz,
                angle        = -theta2,
                deg          = True)
        a.xyz = new_xyz

def fix_rotamer_outliers(
    pdb_hierarchy,
    grm,
    xrs,
    map_data=None,
    radius=5,
    mon_lib_srv=None,
    rotamer_manager=None,
    backrub_range=10,
    asc=None,
    verbose=False,
    log=None):
  import boost.python
  boost.python.import_ext("scitbx_array_family_flex_ext")
  from scitbx_array_family_flex_ext import reindexing_array

  if mon_lib_srv is None:
    mon_lib_srv = mmtbx.monomer_library.server.server()
  if rotamer_manager is None:
    rotamer_manager = RotamerEval(mon_lib_srv=mon_lib_srv)
  if log is None:
    log = sys.stdout
  get_class = iotbx.pdb.common_residue_names_get_class
  assert pdb_hierarchy is not None
  assert grm is not None
  assert xrs is not None
  if asc is None:
    asc = pdb_hierarchy.atom_selection_cache()
  special_position_settings = crystal.special_position_settings(
      crystal_symmetry = xrs.crystal_symmetry())
  n_atoms = pdb_hierarchy.atoms_size()
  hd_sel = xrs.hd_selection()

  crystal_gridding = None
  if map_data is not None:
    crystal_gridding = maptbx.crystal_gridding(
      unit_cell             = xrs.unit_cell(),
      space_group_info      = xrs.space_group_info(),
      pre_determined_n_real = map_data.accessor().all())

  for model in pdb_hierarchy.models():
    for chain in model.chains():
      for conf in chain.conformers():
        residues = conf.residues()
        n_res = len(residues)
        for i_res, res in enumerate(residues):
          if verbose:
            print >> log, "Working on", res.id_str()
          cl = get_class(res.resname)
          if cl not in ["common_amino_acid","modified_amino_acid"]:
            continue
          ev = rotamer_manager.evaluate_residue_2(res)
          if ev != "OUTLIER":
            continue
          sample_backrub_angles = [0]
          if backrub_range is not None:
            inc = 3
            f = 3
            while f <= backrub_range:
              sample_backrub_angles.append(-f)
              sample_backrub_angles.append(f)
              f += inc
          all_inf = []
          sel, bsel_around_no_mc, selection_around_residue = _get_selections_around_residue(
              n_atoms,
              xrs,
              res,
              special_position_settings,
              radius)
          r_a = list(reindexing_array(n_atoms,
              selection_around_residue.iselection().as_int()))
          reindexing_dict = {}
          for i in sel:
            reindexing_dict[i] = r_a[i]
          pdb_selected = pdb_hierarchy.select(selection_around_residue)
          bsel_around_no_mc_selected = bsel_around_no_mc.select(selection_around_residue)
          hd_sel_selected = hd_sel.select(selection_around_residue)
          grm_selected = grm.select(selection_around_residue)
          for backrub_angle in sample_backrub_angles:
            if verbose:
              print >> log, "  Backrub angle:", backrub_angle
            # make backrub, check ramachandran status
            prev_res = None
            if i_res > 0:
              prev_res = residues[i_res-1]
            next_res = None
            if i_res+1 < n_res:
              next_res = residues[i_res+1]
            backrub_move(
                prev_res = prev_res,
                cur_res = res,
                next_res = next_res,
                angle=backrub_angle,
                move_oxygens=False,
                accept_worse_rama=False,
                rotamer_manager=rotamer_manager)
            # sample rotamers
            s_inf = _get_rotamers_evaluated(
                pdb_hierarchy=pdb_selected,
                sel=sel,
                xrs=xrs.select(selection_around_residue),
                crystal_gridding=crystal_gridding,
                bsel_around_no_mc=bsel_around_no_mc_selected,
                hd_sel=hd_sel_selected,
                res=res,
                grm=grm_selected,
                reind_dict=reindexing_dict,
                mon_lib_srv=mon_lib_srv,
                map_data=map_data,
                prefix="%d" % backrub_angle)
            if s_inf is None:
              continue
            all_inf.extend(s_inf)
            all_inf = sorted(all_inf, key=lambda x: (x[5], x[2]))
            if verbose:
              for inf_elem in all_inf:
                print >> log, "    ", inf_elem[:-1]
            # see if need to continue to another backrubs
            # pdb_hierarchy.write_pdb_file(
            #     file_name="%s_%d.pdb" % (res.id_str()[7:], backrub_angle))
            if verbose:
              print >> log, "  The best clashscore 2:", all_inf[-1][5]
            if all_inf[-1][5] > -0.01:
              break
            backrub_move(
                prev_res = prev_res,
                cur_res = res,
                next_res = next_res,
                angle=-backrub_angle,
                move_oxygens=False,
                accept_worse_rama=False,
                rotamer_manager=rotamer_manager)
          if len(all_inf) == 0:
            continue
          if verbose:
            print >> log, "Setting best available rotamer:", all_inf[-1][:-1]
          res.atoms().set_xyz(all_inf[-1][-1])
  return pdb_hierarchy

def switch_rotamers(
      pdb_hierarchy,
      mode,
      accept_allowed=True,
      selection=None,
      mon_lib_srv=None,
      rotamer_manager=None):
  if(mode is None): return pdb_hierarchy
  pdb_hierarchy.reset_atom_i_seqs()
  assert mode in ["max_distant","min_distant","exact_match","fix_outliers"],mode
  from mmtbx.command_line import lockit
  if mon_lib_srv is None:
    mon_lib_srv = mmtbx.monomer_library.server.server()
  sites_cart_start = pdb_hierarchy.atoms().extract_xyz()
  sites_cart_result = sites_cart_start.deep_copy()
  if ((mode == "fix_outliers")
      and rotamer_manager is None):
    rotamer_manager = RotamerEval(mon_lib_srv=mon_lib_srv)
  for model in pdb_hierarchy.models():
    for chain in model.chains():
      for residue_group in chain.residue_groups():
        conformers = residue_group.conformers()
        if(len(conformers)>1): continue # XXX ignore alt conformations
        for conformer in residue_group.conformers():
          residue = conformer.only_residue()
          residue_iselection = flex.size_t()
          for atom in residue.atoms():
            residue_iselection.append(atom.i_seq)
          exclude = False
          if(selection is not None):
            for r_i_seq in residue_iselection:
              if(not selection[r_i_seq]):
                exclude = True
                break
          if mode == "fix_outliers":
            evaluation = rotamer_manager.evaluate_residue_2(residue)
            if evaluation == "Favored":
              exclude = True
            if evaluation == "Allowed" and accept_allowed:
              exclude = True
          if not exclude:
            # print "  Fixing rotamer outlier", residue.id_str()
            rotamer_iterator = lockit.get_rotamer_iterator(
              mon_lib_srv         = mon_lib_srv,
              residue             = residue,
              atom_selection_bool = None)
            if(rotamer_iterator is not None):
              sites_cart_start_ = sites_cart_start.select(residue_iselection)
              distances = flex.double()
              sites = []
              for rotamer, rotamer_sites_cart in rotamer_iterator:
                if not accept_allowed:
                  t_residue = residue.standalone_copy()
                  t_residue.atoms().set_xyz(rotamer_sites_cart)
                  ev = rotamer_manager.evaluate_residue_2(t_residue)
                  if ev == "Allowed":
                    # print "  Skipping allowed for ", residue.id_str()
                    continue
                dist = flex.max(flex.sqrt((
                  sites_cart_start_ - rotamer_sites_cart).dot()))
                distances.append(dist)
                sites.append(rotamer_sites_cart.deep_copy())
              dist_start = -1.
              if(mode in ["min_distant", "exact_match", "fix_outliers"]):
                dist_start = 1.e+6
              res = None
              for d, s in zip(distances, sites):
                if(mode=="min_distant"):
                  if(d<dist_start and d>0.5):
                    dist_start = d
                    res = s.deep_copy()
                elif(mode=="exact_match" or mode=="fix_outliers"):
                  if(d<dist_start):
                    dist_start = d
                    res = s.deep_copy()
                else:
                  if(d>dist_start):
                    res = s.deep_copy()
                    dist_start = d
              if(res is None and mode=="min_distant"):
                dist_start = 1.e+6
                for d, s in zip(distances, sites):
                  if(d<dist_start):
                    dist_start = d
                    res = s.deep_copy()
              assert res is not None
              sites_cart_result = sites_cart_result.set_selected(
                residue_iselection, res)
  pdb_hierarchy.atoms().set_xyz(sites_cart_result)
  return pdb_hierarchy

def seg_id_to_chain_id(pdb_hierarchy):
  import string
  two_character_chain_ids = []
  segid_list = []
  seg_dict = {}
  for atom in pdb_hierarchy.atoms():
    if atom.segid not in segid_list:
      segid_list.append(atom.segid)
  lower_letters = string.lowercase
  upper_letters = string.uppercase
  two_character_chain_ids = generate_two_character_ids()
  for id in segid_list:
    chainID = two_character_chain_ids[0]
    seg_dict[id] = chainID
    two_character_chain_ids.remove(chainID)
  return seg_dict

def find_bare_chains_with_segids(pdb_hierarchy):
  bare_chains = False
  for chain in pdb_hierarchy.chains():
    if chain.id in ['', ' ', '  ']:
      segid = None
      for atom in chain.atoms():
        if segid == None:
          segid = atom.segid
        elif segid != None and segid != atom.segid:
          #require that each chain have a unique segid for this logic
          return False
      if segid != None and segid not in ['', ' ', '  ', '   ', '    ']:
        bare_chains = True
  return bare_chains

def assign_chain_ids(pdb_hierarchy, seg_dict):
  rename_txt = ""
  for chain in pdb_hierarchy.chains():
    if chain.id in ['', ' ', '  ']:
      segid = None
      for atom in chain.atoms():
        if segid == None:
          segid = atom.segid
        elif segid != atom.segid:
          print segid, atom.segid
          raise Sorry("multiple segid values defined for chain")
      new_id = seg_dict[segid]
      chain.id = new_id
      rename_txt = rename_txt + \
      "segID %s renamed chain %s for Reduce N/Q/H analysis\n" % (segid, new_id)
  return rename_txt

def check_for_duplicate_chain_ids(pdb_hierarchy):
  used_chain_ids = []
  for model in pdb_hierarchy.models():
    for chain in model.chains():
      found_conformer = False
      for conformer in chain.conformers():
        if not conformer.is_protein() and not conformer.is_na():
          continue
        else:
          found_conformer = True
      if not found_conformer:
        continue
      cur_id = chain.id
      if cur_id not in used_chain_ids:
        used_chain_ids.append(cur_id)
      else:
        return True
  return False

def force_unique_chain_ids(pdb_hierarchy):
  used_chain_ids = []
  two_char = generate_two_character_ids()
  #filter all used chains
  for model in pdb_hierarchy.models():
    for chain in model.chains():
      cur_id = chain.id
      if cur_id in two_char:
        two_char.remove(cur_id)
  #force unique chain ids
  for model in pdb_hierarchy.models():
    for chain in model.chains():
      cur_id = chain.id
      if cur_id not in used_chain_ids:
        used_chain_ids.append(cur_id)
      else:
        new_id = two_char[0]
        chain.id = new_id
        two_char.remove(new_id)

def generate_two_character_ids():
  import string
  singles = []
  two_character_chain_ids = []
  for ch in string.uppercase:
    singles.append(ch)
  for num in range(10):
    ch = "%d" % num
  for ch in string.lowercase:
    singles.append(ch)
    singles.append(ch)
  for i in range(len(singles)):
    ch = singles[i]
    two_character_chain_ids.append(ch)
  for i in range(len(singles)):
    for j in range(len(singles)):
      ch = singles[i]+singles[j]
      two_character_chain_ids.append(ch)
  return two_character_chain_ids

def equivalent_sigma_from_cumulative_histogram_match(
      map_1, map_2, sigma_1, tail_cutoff=3, step=1, verbose=True):
  size_1 = map_1.size()
  size_2 = map_2.size()
  #
  assert size_1 == size_2
  #
  fmt = "%5.2f %6.2f %6.2f"
  if(verbose): print flex.min(map_1), flex.min(map_2)
  start = max(-tail_cutoff*100,int(min(flex.min(map_1), flex.min(map_2)))*100)
  end   = min(tail_cutoff*100+step,int(max(flex.max(map_1), flex.max(map_2)))*100)
  sigmas = flex.double()
  c_1 = flex.double()
  c_2 = flex.double()
  for sig in [i/100. for i in range(start,end,step)]:
    s_a = (map_1>=sig).count(True)*100./size_1
    s_o = (map_2>=sig).count(True)*100./size_2
    if(verbose): print fmt % (sig, s_o, s_a)
    sigmas.append(sig)
    c_1.append(s_a)
    c_2.append(s_o)
  #
  if(verbose): print
  #
  s = flex.sort_permutation(flex.abs(sigmas-sigma_1))
  tmp1 = c_1.select(s)[0]
  s = flex.sort_permutation(flex.abs(c_2-tmp1))
  tmp1 = c_2.select(s)[0]
  tmp2 = sigmas.select(s)[0]
  #
  if(verbose): print tmp1, tmp2
  #
  return tmp2

def optimize_h(fmodel, mon_lib_srv, pdb_hierarchy=None, model=None, log=None,
      verbose=True):
  assert [pdb_hierarchy, model].count(None)==1
  if(log is None): log = sys.stdout
  if(fmodel.xray_structure.hd_selection().count(True)==0): return
  if(verbose):
    print >> log
    print >> log, "Optimizing scattering from H..."
    print >> log, "  before optimization: r_work=%6.4f r_free=%6.4f"%(
    fmodel.r_work(), fmodel.r_free())
  if(model is not None):
    assert_xray_structures_equal(
      x1 = fmodel.xray_structure,
      x2 = model.xray_structure)
    model.reset_occupancies_for_hydrogens()
  if(model is not None): pdb_hierarchy = model.pdb_hierarchy()
  import mmtbx.hydrogens
  rmh_sel = mmtbx.hydrogens.rotatable(pdb_hierarchy = pdb_hierarchy,
    mon_lib_srv=mon_lib_srv, restraints_manager=model.restraints_manager)
  # XXX inefficient
  rmh_sel_i_seqs = flex.size_t()
  for i in rmh_sel:
    for ii in i[1]:
      rmh_sel_i_seqs.append(ii)
  fmodel.xray_structure.set_occupancies(value = 0, selection = rmh_sel_i_seqs)
  fmodel.update_f_hydrogens(log=log)
  if(model is not None):
    model.xray_structure = fmodel.xray_structure
  fmodel.xray_structure.set_occupancies(value = 0,
    selection = fmodel.xray_structure.hd_selection())
  if(model is not None):
    model.xray_structure = fmodel.xray_structure
  if(verbose):
    print >> log, "  after optimization:  r_work=%6.4f r_free=%6.4f"%(
      fmodel.r_work(), fmodel.r_free())
  #

class set_map_to_value(object):
  def __init__(self, map_data, xray_structure, atom_radius, value):
    adopt_init_args(self, locals())
    sites_cart = self.xray_structure.sites_cart()
    selection = maptbx.grid_indices_around_sites(
      unit_cell  = self.xray_structure.unit_cell(),
      fft_n_real = self.map_data.focus(),
      fft_m_real = self.map_data.all(),
      sites_cart = sites_cart,
      site_radii = flex.double(sites_cart.size(), self.atom_radius))
    sel_ = flex.bool(size=self.map_data.size(), iselection=selection)
    sel_.reshape(self.map_data.accessor())
    self.map_data = self.map_data.set_selected(sel_, self.value)

  def write_xplor_map(self, file_name):
    unit_cell = self.xray_structure.unit_cell()
    sites_frac = self.xray_structure.sites_frac()
    frac_max = sites_frac.max()
    frac_min = sites_frac.min()
    frac_max = list(flex.double(frac_max))
    frac_min = list(flex.double(frac_min))
    n_real = self.map_data.all()
    gridding_first=[ifloor(f*n) for f,n in zip(frac_min,n_real)]
    gridding_last=[iceil(f*n) for f,n in zip(frac_max,n_real)]
    gridding = iotbx.xplor.map.gridding(n = self.map_data.focus(),
      first = gridding_first, last = gridding_last)
    iotbx.xplor.map.writer(
      file_name          = file_name,
      is_p1_cell         = True,
      title_lines        = [' None',],
      unit_cell          = unit_cell,
      gridding           = gridding,
      data               = self.map_data,
      average            = -1,
      standard_deviation = -1)

class shift_origin(object):
  def __init__(self, map_data, pdb_hierarchy, crystal_symmetry):
    self.pdb_hierarchy = pdb_hierarchy
    self.crystal_symmetry = crystal_symmetry
    self.map_data = map_data
    self.shift = None
    shift_needed = not \
      (map_data.focus_size_1d() > 0 and map_data.nd() == 3 and
       map_data.is_0_based())
    if(shift_needed):
      if(not crystal_symmetry.space_group().type().number() in [0,1]):
        raise RuntimeError("Not implemented")
      a,b,c = crystal_symmetry.unit_cell().parameters()[:3]
      N = map_data.all()
      O=map_data.origin()
      sites_cart = self.pdb_hierarchy.atoms().extract_xyz()
      fm = crystal_symmetry.unit_cell().fractionalization_matrix()
      self.map_data = self.map_data.shift_origin()
      t_best=-9999.
      sites_cart_best = None
      for inc1 in [0]:#[-2,-1,0,1,2]:
        for inc2 in [0]:#[-2,-1,0,1,2]:
          N_ = (N[0]-inc1,N[1]-inc1,N[2]-inc1)
          O_ = (O[0]-inc2,O[1]-inc2,O[2]-inc2)

          sx,sy,sz = O_[0]/N_[0],O_[1]/N_[1], O_[2]/N_[2]
          self.shift= crystal_symmetry.unit_cell().orthogonalize([sx,sy,sz])

          sites_cart_shifted = sites_cart+\
            flex.vec3_double(sites_cart.size(), [-self.shift[0],-self.shift[1],-self.shift[2]])
          sites_frac_shifted = fm*sites_cart_shifted
          t = maptbx.map_sum_at_sites_frac(map_data=self.map_data,
            sites_frac=sites_frac_shifted)
          #print inc1, inc2, t
          if(t>t_best):
            t_best=t
            sites_cart_best=sites_cart_shifted.deep_copy()
      if(sites_cart_best is not None):
        self.pdb_hierarchy.atoms().set_xyz(sites_cart_best)

  def shift_back(self, pdb_hierarchy):
    sites_cart = pdb_hierarchy.atoms().extract_xyz()
    sites_cart_shifted = sites_cart+\
      flex.vec3_double(sites_cart.size(), self.shift)
    pdb_hierarchy.atoms().set_xyz(sites_cart_shifted)

  def show_shifted(self):
    self.pdb_hierarchy.write_pdb_file(file_name="origin_shifted.pdb",
      crystal_symmetry=self.crystal_symmetry)
    from iotbx import ccp4_map
    ccp4_map.write_ccp4_map(
      file_name="origin_shifted.ccp4",
      unit_cell=self.crystal_symmetry.unit_cell(),
      space_group=self.crystal_symmetry.space_group(),
      #gridding_first=(0,0,0),# This causes a bug (map gets shifted)
      #gridding_last=n_real,  # This causes a bug (map gets shifted)
      map_data=self.map_data,
      labels=flex.std_string([""]))

class extract_box_around_model_and_map(object):
  def __init__(self,
               xray_structure,
               map_data,
               box_cushion,
               selection=None,
               density_select=None,
               threshold=None,
               mask_atoms=False,
               mask_atoms_atom_radius=3.0):
    adopt_init_args(self, locals())
    cs = xray_structure.crystal_symmetry()
    self.initial_shift = None
    self.initial_shift_cart = None
    self.total_shift_cart = None
    ## Make sure map has origin at (0,0,0), that is zero_based. Otherwise shift
    ## origin.
    shift_needed = not \
      (map_data.focus_size_1d() > 0 and map_data.nd() == 3 and
       map_data.is_0_based())
    if(shift_needed):
      if(not cs.space_group().type().number() in [0,1]):
        raise RuntimeError("Shift for space group other than 0 or 1 not implemented")
      # Map origin is not at (0,0,0) and it is P1
      # shifting map only
      N_ = self.map_data.all()
      O_ = self.map_data.origin()
      sx,sy,sz = O_[0]/N_[0],O_[1]/N_[1], O_[2]/N_[2]
      self.initial_shift  = [-sx,-sy,-sz]
      self.initial_shift_cart = cs.unit_cell().orthogonalize(self.initial_shift)
      self.map_data=self.map_data.shift_origin()
      # shift model
      sites_cart = xray_structure.sites_cart()
      sites_cart_shifted = sites_cart+\
          flex.vec3_double(sites_cart.size(), self.initial_shift_cart)
      xray_structure.set_sites_cart(sites_cart = sites_cart_shifted)
    if(selection is None):
      selection = flex.bool(xray_structure.scatterers().size(), True)
    xray_structure_selected = xray_structure.select(selection=selection)
    cushion = flex.double(cs.unit_cell().fractionalize((box_cushion,)*3))
    if(density_select):
      frac_min,frac_max=self.select_box(
        threshold = threshold, xrs = xray_structure_selected)
      frac_max = list(flex.double(frac_max)+cushion)
      frac_min = list(flex.double(frac_min)-cushion)
      for kk in xrange(3):
        frac_min[kk]=max(0.,frac_min[kk])
        frac_max[kk]=min(1.-1./map_data.all()[kk], frac_max[kk])
    else:
      self.pdb_outside_box_msg=""
      frac_min = xray_structure_selected.sites_frac().min()
      frac_max = xray_structure_selected.sites_frac().max()
      frac_max = list(flex.double(frac_max)+cushion)
      frac_min = list(flex.double(frac_min)-cushion)
    self.frac_min = frac_min
    na = self.map_data.all()
    gridding_first=[ifloor(f*n) for f,n in zip(frac_min,na)]
    gridding_last=[iceil(f*n) for f,n in zip(frac_max,na)]
    all = self.map_data.all()
    self.map_box = maptbx.copy(self.map_data, gridding_first, gridding_last)
    o = self.map_box.origin()
    self.secondary_shift = (-o[0]/all[0],-o[1]/all[1],-o[2]/all[2])
    self.secondary_shift_cart = cs.unit_cell().orthogonalize(self.secondary_shift)
    ps=self.initial_shift_cart
    ss=self.secondary_shift_cart
    if ps is None:
      self.total_shift_cart=ss
    elif ss is None:
      self.total_shift_cart=ps
    else:
      self.total_shift_cart = (ps[0]+ss[0], ps[1]+ss[1], ps[2]+ss[2])
    self.map_box.reshape(flex.grid(self.map_box.all()))
    # shrink unit cell to match the box
    p = cs.unit_cell().parameters()
    abc = []
    for i in range(3):
      abc.append( p[i] * self.map_box.all()[i]/na[i] )
    new_unit_cell_box = uctbx.unit_cell(
      parameters=(abc[0],abc[1],abc[2],p[3],p[4],p[5]))
    cs = crystal.symmetry(unit_cell=new_unit_cell_box, space_group="P1")
    self.box_crystal_symmetry=cs
    sp = crystal.special_position_settings(cs)
    # new xray_structure in the box
    sites_frac_new = xray_structure_selected.sites_frac()+self.secondary_shift
    xray_structure_box=xray_structure_selected.replace_sites_frac(sites_frac_new)
    sites_cart = xray_structure_box.sites_cart()
    sites_frac = new_unit_cell_box.fractionalize(sites_cart)
    xray_structure_box = xray_structure_box.replace_sites_frac(sites_frac)
    self.xray_structure_box = xray.structure(
       sp,xray_structure_box.scatterers())
    # shift to map (boxed) sites back
    sc1 = xray_structure_selected.sites_cart()
    sc2 = self.xray_structure_box.sites_cart()
    if sc1.size()>0:
      self.shift_to_map_boxed_sites_back = (sc1-sc2)[0]
    else:
      self.shift_to_map_boxed_sites_back = None
    ###
    if(mask_atoms):
      import boost.python
      cctbx_maptbx_ext = boost.python.import_ext("cctbx_maptbx_ext")
      radii = flex.double(
        self.xray_structure_box.sites_frac().size(), mask_atoms_atom_radius)
      mask = cctbx_maptbx_ext.mask(
        sites_frac                  = self.xray_structure_box.sites_frac(),
        unit_cell                   = self.xray_structure_box.unit_cell(),
        n_real                      = self.map_box.all(),
        mask_value_inside_molecule  = 1,
        mask_value_outside_molecule = 0,
        radii                       = radii)
      self.map_box = self.map_box*mask
    ###

  def select_box(self,threshold,xrs=None):
    # Select box where data are positive (> threshold*max)
    map_data=self.map_data
    origin=list(map_data.origin())
    assert origin==[0,0,0]
    all=list(map_data.all())
    # Get max value vs x,y,z
    value_list=flex.double()
    for i in xrange(0,all[0]):
      new_map_data = maptbx.copy(map_data,
         tuple((i,0,0)),
         tuple((i,all[1],all[2]))
       )
      value_list.append(new_map_data.as_1d().as_double().min_max_mean().max)
    x_min,x_max=self.get_range(value_list,threshold=threshold)

    value_list=flex.double()
    for j in xrange(0,all[1]):
      new_map_data = maptbx.copy(map_data,
         tuple((0,j,0)),
         tuple((all[0],j,all[2]))
       )
      value_list.append(new_map_data.as_1d().as_double().min_max_mean().max)
    y_min,y_max=self.get_range(value_list,threshold=threshold)

    value_list=flex.double()
    for k in xrange(0,all[2]):
      new_map_data = maptbx.copy(map_data,
         tuple((0,0,k)),
         tuple((all[0],all[1],k))
       )
      value_list.append(new_map_data.as_1d().as_double().min_max_mean().max)
    z_min,z_max=self.get_range(value_list,threshold=threshold)

    frac_min=(x_min,y_min,z_min)
    frac_max=(x_max,y_max,z_max)

    self.pdb_outside_box_msg=""
    if xrs is not None and xrs.sites_frac().size()>0:
      # warn if outside box chosen
      c_min= xrs.sites_frac().min()
      c_max= xrs.sites_frac().max()
      if c_min[0]<frac_min[0] or \
         c_min[1]<frac_min[1] or \
         c_min[2]<frac_min[2] or \
         c_max[0]>frac_max[0] or \
         c_max[1]>frac_max[1] or \
         c_max[2]>frac_max[2]:
       cs=xrs.crystal_symmetry()
       self.pdb_outside_box_msg="""
NOTE: Output model is not contained in box.
Range for model: %7.1f  %7.1f  %7.1f   to %7.1f  %7.1f  %7.1f
Range for box:   %7.1f  %7.1f  %7.1f   to %7.1f  %7.1f  %7.1f""" %(
     cs.unit_cell().orthogonalize(c_min)+
     cs.unit_cell().orthogonalize(c_max)+
     cs.unit_cell().orthogonalize(frac_min)+
     cs.unit_cell().orthogonalize(frac_max))
    return frac_min,frac_max

  def get_range(self, value_list, threshold=None, ignore_ends=True,
     keep_near_ends_frac=0.02):
    # ignore ends allows ignoring the first and last points which may be off
    if threshold is None: threshold=0
    n_tot=value_list.size()
    assert n_tot>0
    min_value=value_list.min_max_mean().min
    max_value=value_list.min_max_mean().max
    cutoff=min_value+(max_value-min_value)*threshold
    if ignore_ends:
      i_start=1
    else:
      i_start=0
    i_low=None
    for i in xrange(i_start,n_tot):
      if value_list[i]>cutoff:
        i_low=i
        break
    i_high=None
    for i in xrange(i_start,n_tot):
      ii=n_tot-i
      if value_list[ii]>cutoff:
        i_high=ii
        break
    if i_low is None or i_high is None:
      raise Sorry("Cannot auto-select region...please supply PDB file")
    if i_low/n_tot<keep_near_ends_frac: i_low=0
    if (n_tot-1-i_high)/n_tot<keep_near_ends_frac: i_high=n_tot-1
    return i_low/n_tot,i_high/n_tot

  def write_xplor_map(self, file_name="box.xplor"):
    gridding = iotbx.xplor.map.gridding(
      n     = self.map_box.focus(),
      first = (0,0,0),
      last  = self.map_box.focus())
    iotbx.xplor.map.writer(
      file_name          = file_name,
      is_p1_cell         = True,
      title_lines        = ['Map in box',],
      unit_cell          = self.xray_structure_box.unit_cell(),
      gridding           = gridding,
      data               = self.map_box.as_double(),
      average            = -1,
      standard_deviation = -1)

  def write_ccp4_map(self, file_name="box.ccp4"):
    from iotbx import ccp4_map
    ccp4_map.write_ccp4_map(
      file_name      = file_name,
      unit_cell      = self.xray_structure_box.unit_cell(),
      space_group    = self.xray_structure_box.space_group(),
      map_data       = self.map_box.as_double(),
      labels=flex.std_string([" "]))

  def box_map_coefficients_as_fft_map(self, d_min, resolution_factor):
    box_map_coeffs = self.box_map_coefficients(d_min = d_min)
    fft_map = box_map_coeffs.fft_map(resolution_factor=resolution_factor)
    fft_map.apply_sigma_scaling()
    return fft_map

  def box_map_coefficients(self, d_min):
    from scitbx import fftpack
    fft = fftpack.real_to_complex_3d([i for i in self.map_box.all()])
    map_box = maptbx.copy(
      self.map_box, flex.grid(fft.m_real()).set_focus(self.map_box.focus()))
    map_box.reshape(flex.grid(fft.m_real()).set_focus(fft.n_real()))
    map_box = fft.forward(map_box)
    cs = self.xray_structure_box.crystal_symmetry()
    box_structure_factors = maptbx.structure_factors.from_map(
      unit_cell=cs.unit_cell(),
      space_group_type=cs.space_group().type(),
      anomalous_flag=False,
      d_min=d_min,
      complex_map=map_box,
      conjugate_flag=True,
      discard_indices_affected_by_aliasing=True)
    n = map_box.all()[0] * map_box.all()[1] * map_box.all()[2]
    box_map_coeffs = miller.set(
      crystal_symmetry=cs,
      anomalous_flag=False,
      indices=box_structure_factors.miller_indices(),
      ).array(data=box_structure_factors.data()/n)
    return box_map_coeffs

  def map_coefficients(self, d_min, resolution_factor, file_name="box.mtz"):
    box_map_coeffs = self.box_map_coefficients(d_min = d_min)
    if(file_name is not None):
      mtz_dataset = box_map_coeffs.as_mtz_dataset(column_root_label="BoxMap")
      mtz_object = mtz_dataset.mtz_object()
      mtz_object.write(file_name = file_name)
    return box_map_coeffs

class experimental_data_target_and_gradients(object):
  def __init__(self, fmodel, alpha_beta=None):
    self.fmodel = fmodel
    size = self.fmodel.xray_structure.scatterers().size()
    self.sel = flex.bool(size, True).iselection()
    self.target_functor = self.fmodel.target_functor(
      alpha_beta = alpha_beta)(compute_gradients=True)

  def update_xray_structure(self, xray_structure, alpha_beta=None):
    self.fmodel.update_xray_structure(xray_structure = xray_structure,
      update_f_calc=True)
    self.target_functor = self.fmodel.target_functor(
      alpha_beta = alpha_beta)(compute_gradients=True)

  def grad_occ(self):
    self.fmodel.xray_structure.scatterers().flags_set_grads(state=False)
    self.fmodel.xray_structure.scatterers().flags_set_grad_occupancy(
      iselection = self.sel)
    return self.target_functor.gradients_wrt_atomic_parameters(occupancy=True)

  def grad_sites_cart(self):
    self.fmodel.xray_structure.scatterers().flags_set_grads(state=False)
    return self.target_functor.d_target_d_site_cart()

  def target(self):
    return self.target_functor.target_work()

  def show(self, log=None):
    if(log is None): log = sys.stdout
    print >> log, "Target type and value: %s %-15.6f" %(self.fmodel.target_name,
      self.target())
    print >> log, "r_work=%6.4f r_free=%6.4f" % (self.fmodel.r_work(),
      self.fmodel.r_free())
    go = self.grad_occ()
    gs = self.grad_sites_cart()
    sites_cart = self.fmodel.xray_structure.sites_cart()
    print >> log, "                                          Gradients"
    print >> log, "                sites_cart   occ   b_iso      occ                 sites_cart"
    fmt="%8.3f %8.3f %8.3f %5.2f %7.2f %8.4f %8.4f %8.4f %8.4f"
    for i, sc in enumerate(self.fmodel.xray_structure.scatterers()):
      print >> log, fmt%(sites_cart[i][0], sites_cart[i][1], sites_cart[i][2],
        sc.occupancy,adptbx.u_as_b(sc.u_iso), go[i], gs[i][0],gs[i][1],gs[i][2])

  def group_occupancy_grads(
        self,
        pdb_hierarchy=None,
        residues_per_window=None,
        selections=None):
    pair = [pdb_hierarchy, residues_per_window]
    assert pair.count(None) in [0,2]
    if(selections is None): assert pair.count(None)==0
    else: assert pair.count(None)==2
    if(pair.count(None)==0): assert selections is None
    result = []
    occ_grads = self.grad_occ()
    if(selections is None):
      assert_xray_structures_equal(
        x1 = self.fmodel.xray_structure,
        x2 = pdb_hierarchy.extract_xray_structure(
          crystal_symmetry=self.fmodel.xray_structure.crystal_symmetry()),
        sites = False,
        adp = False,
        occupancies = False,
        elements = True,
        scattering_types = False)
      selections = pdb_hierarchy.chunk_selections(
        residues_per_chunk=residues_per_window)
      for sel in selections:
        h = pdb_hierarchy.select(sel)
        rgs = list(h.residue_groups())
        assert len(rgs)>0
        rg1 = rgs[0]
        rg2 = rgs[len(rgs)-1]
        chains = list(h.chains())
        assert len(chains)==1
        chain_id = chains[0].id
        info_str = "_".join([i.strip() for i in [chain_id,rg1.resseq,rg2.resseq]])
        group_occ_grad = flex.sum(occ_grads.select(sel))
        result.append([info_str,group_occ_grad])
    else:
      for sel in selections:
        group_occ_grad = flex.sum(occ_grads.select(sel))
        result.append([None,group_occ_grad])
    return result

class states(object):
  def __init__(self, pdb_hierarchy, xray_structure=None, counter=0):
    adopt_init_args(self, locals())
    self.counter = counter
    self.root = iotbx.pdb.hierarchy.root()
    self.sites_carts = []

  def add(self, sites_cart):
    self.sites_carts.append(sites_cart)
    ph = self.pdb_hierarchy.deep_copy()
    if(self.xray_structure is not None):
      xrs = self.xray_structure.replace_sites_cart(new_sites = sites_cart)
      ph.adopt_xray_structure(xrs)
    else:
      ph.atoms().set_xyz(sites_cart)
    models = ph.models()
    md = models[0].detached_copy()
    md.id = str(self.counter)
    self.root.append_model(md)
    self.counter += 1

  def write(self, file_name, crystal_symmetry=None):
    if(crystal_symmetry is None):
      if(self.xray_structure is not None):
        crystal_symmetry = self.xray_structure.crystal_symmetry()
    if([crystal_symmetry,self.xray_structure].count(None)==0):
      assert crystal_symmetry.is_similar_symmetry(
        self.xray_structure.crystal_symmetry())
    self.root.write_pdb_file(
      file_name        = file_name,
      crystal_symmetry = crystal_symmetry)

def structure_factors_from_map(map_data, unit_cell_lengths, n_real,
                               crystal_symmetry, resolution_factor=1/4.):
  d_min_guess_from_map = maptbx.d_min_from_map(map_data=map_data,
    unit_cell=crystal_symmetry.unit_cell(), resolution_factor=resolution_factor)
  complete_set = miller.build_set(
    crystal_symmetry = crystal_symmetry,
    anomalous_flag   = False,
    d_min            = d_min_guess_from_map)
  return complete_set.structure_factors_from_map(
    map            = map_data,
    use_scale      = True,
    anomalous_flag = False,
    use_sg         = True)

class f_000(object):
  def __init__(self, xray_structure=None, unit_cell_volume=None,
               solvent_fraction=None, mean_solvent_density=0.35):
    if(solvent_fraction is not None):
      assert solvent_fraction>=0 and solvent_fraction<=1
    f_000 = 0
    if(xray_structure is not None):
      unit_cell_volume = xray_structure.unit_cell().volume()
      f_000 += xray_structure.f_000()
      if(solvent_fraction is None):
        import mmtbx.masks
        solvent_fraction = mmtbx.masks.asu_mask(xray_structure=xray_structure,
          d_min=1).asu_mask.contact_surface_fraction
    if(solvent_fraction is not None):
      f_000 += solvent_fraction*unit_cell_volume*mean_solvent_density
    if(f_000 == 0):
      f_000 = unit_cell_volume*mean_solvent_density
    self.f_000 = f_000
    self.solvent_fraction = solvent_fraction

class detect_hydrogen_nomenclature_problem (object) :
  """
  This allows us to avoid the following problems:
  1) a bug in automatic linking which deletes the monomer library definition
     for HD22 for an N-linked Asn, even though it may not actually be replaced
     by a sugar link.
  2) general issues with hydrogen nomenclature

  Attributes
  ----------
  bad_hydrogens: a list of problematic atom ID strings
  n_asn_hd22: number of inappropriate ASN HD22 atoms
  n_hydrogen: number of hydrogens missing geometry restraints
  n_other: number of non-hydrogen atoms missing geometry restraints
  """
  def __init__ (self, pdb_file, cif_files=()) :
    args = [ pdb_file, ] + list(cif_files)
    import mmtbx.monomer_library.server
    mon_lib_srv = mmtbx.monomer_library.server.server()
    ener_lib = mmtbx.monomer_library.server.ener_lib()
    params = mmtbx.monomer_library.pdb_interpretation.master_params.extract()
    params.automatic_linking.link_all=True
    processed_pdb_file = mmtbx.monomer_library.pdb_interpretation.run(
      args=args,
      params=params,
      strict_conflict_handling=False,
      substitute_non_crystallographic_unit_cell_if_necessary=True,
      log=null_out())
    all_chain_proxies = processed_pdb_file.all_chain_proxies
    pdb_atoms = all_chain_proxies.pdb_atoms
    nb_reg = all_chain_proxies.nonbonded_energy_type_registry
    self.bad_hydrogens = []
    self.n_asn_hd22 = 0
    self.n_hydrogen = 0
    self.n_other = 0
    if (nb_reg.n_unknown_type_symbols() > 0) :
      unknown_atoms = nb_reg.get_unknown_atoms(pdb_atoms)
      for atom in unknown_atoms :
        print atom.quote()
        labels = atom.fetch_labels()
        if (atom.name == "HD22") and (labels.resname == "ASN") :
          self.n_asn_hd22 += 1
          self.bad_hydrogens.append(atom.id_str())
        elif (atom.element.strip() == "H") :
          self.n_hydrogen += 1
          self.bad_hydrogens.append(atom.id_str())
        else :
          self.n_other += 1
