import libtbx.load_env
has_antlr3 = libtbx.env.has_module('antlr3')

if has_antlr3:
  import boost.python
  ext = boost.python.import_ext("iotbx_cif_ext")

from cctbx import adptbx, crystal
from cctbx.xray import structure
from iotbx.cif import model, builders
from libtbx.containers import OrderedDict

import sys

class reader:

  def __init__(self, file_path=None, file_object=None, input_string=None,
               builder=None, max_errors=50):
    assert [file_path, file_object, input_string].count(None) == 2
    assert has_antlr3
    if builder is None:
      builder = builders.cif_model_builder()
    self.builder = builder
    if file_object is not None:
      input_string = file_object.read()
    if input_string is not None:
      self.parser = ext.fast_reader(input_string, builder)
    if file_path is not None:
      self.parser = ext.fast_reader(file_path, builder)
    self.show_errors(max_errors)

  def model(self):
    return self.builder.model()

  def error_count(self):
    return self.parser.lexer_errors().size()\
           + self.parser.parser_errors().size()

  def show_errors(self, max_errors=50, out=None):
    if out is None: out = sys.stdout
    for msg in self.parser.lexer_errors()[:max_errors]:
      print >> out, msg
    for msg in self.parser.parser_errors()[:max_errors]:
      print >> out, msg

fast_reader = reader # XXX backward compatibility 2010-08-25

class crystal_symmetry_as_cif_block:

  def __init__(self, crystal_symmetry):
    self.cif_block = model.block()
    sym_loop = model.loop(data={
      '_space_group_symop_operation_xyz':
      [s.as_xyz() for s in crystal_symmetry.space_group()],
      '_space_group_symop_id':
      range(1, len(crystal_symmetry.space_group())+1)})
    self.cif_block.add_loop(sym_loop)
    #
    sg_type = crystal_symmetry.space_group_info().type()
    sg = sg_type.group()
    self.cif_block['_space_group_crystal_system'] = sg.crystal_system()
    self.cif_block['_space_group_IT_number'] = sg_type.number()
    self.cif_block['_space_group_name_H-M_alt'] = sg_type.universal_hermann_mauguin_symbol()
    self.cif_block['_space_group_name_Hall'] = sg_type.hall_symbol()
    #
    uc = crystal_symmetry.unit_cell()
    a,b,c,alpha,beta,gamma = uc.parameters()
    self.cif_block['_cell_length_a'] = a
    self.cif_block['_cell_length_b'] = b
    self.cif_block['_cell_length_c'] = c
    self.cif_block['_cell_angle_alpha'] = alpha
    self.cif_block['_cell_angle_beta'] = beta
    self.cif_block['_cell_angle_gamma'] = gamma


class xray_structure_as_cif_block(crystal_symmetry_as_cif_block):
  sources = {
    "it1992": "International Tables Volume C Table 6.1.1.4 (pp. 500-502)",
    "wk1995": "Waasmaier & Kirfel (1995), Acta Cryst. A51, 416-431",
  }

  def __init__(self, xray_structure):
    crystal_symmetry_as_cif_block.__init__(
      self, xray_structure.crystal_symmetry())
    scatterers = xray_structure.scatterers()
    atom_site_loop = model.loop(header=(
      '_atom_site_label', '_atom_site_type_symbol',
      '_atom_site_fract_x', '_atom_site_fract_y', '_atom_site_fract_z',
      '_atom_site_U_iso_or_equiv', '_atom_site_occupancy'))
    uc = xray_structure.unit_cell()
    fp_fdp_table = {}
    for sc in scatterers:
      atom_site_loop.add_row((
        sc.label, sc.scattering_type, sc.site[0], sc.site[1], sc.site[2],
        sc.u_iso_or_equiv(uc), sc.occupancy))
      fp_fdp_table.setdefault(sc.scattering_type, (sc.fp, sc.fdp))
    aniso_scatterers = scatterers.select(scatterers.extract_use_u_aniso())
    aniso_loop = model.loop(header=('_atom_site_aniso_label',
                                    '_atom_site_aniso_U_11',
                                    '_atom_site_aniso_U_22',
                                    '_atom_site_aniso_U_33',
                                    '_atom_site_aniso_U_12',
                                    '_atom_site_aniso_U_13',
                                    '_atom_site_aniso_U_23'))
    for sc in aniso_scatterers:
      u_cif = adptbx.u_star_as_u_cif(uc, sc.u_star)
      aniso_loop.add_row((
        sc.label, u_cif[0], u_cif[1], u_cif[2], u_cif[3], u_cif[4], u_cif[5]))

    self.cif_block.add_loop(atom_site_loop)
    self.cif_block.add_loop(aniso_loop)
    #
    atom_type_loop = model.loop(header=('_atom_type_symbol',
                                        '_atom_type_scat_dispersion_real',
                                        '_atom_type_scat_dispersion_imag',
                                        '_atom_type_scat_source'))
    scattering_type_registry = xray_structure.scattering_type_registry()
    params = xray_structure.scattering_type_registry_params
    scat_source = self.sources.get(params.table)
    scattering_type_registry = xray_structure.scattering_type_registry()
    for atom_type, gaussian in scattering_type_registry.as_type_gaussian_dict().iteritems():
      if params.custom_dict and atom_type in params.custom_dict:
        scat_source = "Custom %i-Gaussian" %gaussian.n_terms()
      elif scat_source is None:
        scat_source = """\
%i-Gaussian fit: Grosse-Kunstleve RW, Sauter NK, Adams PD:
Newsletter of the IUCr Commission on Crystallographic Computing 2004, 3, 22-31."""
        scat_source = scat_source %gaussian.n_terms()
      fp, fdp = fp_fdp_table[atom_type]
      atom_type_loop.add_row((atom_type, "%.5f" %fp, "%.5f" %fdp, scat_source))
    self.cif_block.add_loop(atom_type_loop)


class miller_indices_as_cif_loop:

  def __init__(self, indices, prefix='_refln'):
    self.refln_loop = model.loop(header=(
      '%s_index_h' %prefix, '%s_index_k' %prefix, '%s_index_l' %prefix))
    for hkl in indices:
      self.refln_loop.add_row(hkl)

class miller_array_as_cif_block(crystal_symmetry_as_cif_block,
                                miller_indices_as_cif_loop):

  def __init__(self, array, array_type):
    assert array_type in ('calc', 'meas')
    crystal_symmetry_as_cif_block.__init__(self, array.crystal_symmetry())
    miller_indices_as_cif_loop.__init__(self, array.indices())
    if array.is_complex_array():
      columns = {'_refln_F_%s' %array_type: flex.abs(array.data()),
                 '_refln_phase_%s' %array_type: array.phases()}
    else:
      if array.is_xray_intensity_array():
        obs_ext = 'squared_'
      else: obs_ext = ''
      columns = OrderedDict({'_refln_F_%s%s' %(obs_ext, array_type):
                 array.data().as_string()})
      if array.sigmas() is not None:
        columns['_refln_F_%ssigma' %(obs_ext)] = array.sigmas().as_string()
      self.refln_loop.add_columns(columns)
    self.cif_block.add_loop(self.refln_loop)


def cctbx_data_structure_from_cif(
  file_object=None, file_path=None, data_structure_builder=None,
  block_heading=None, **kwds):
  assert data_structure_builder is not None
  cif_model = reader(file_path=file_path, file_object=file_object).model()
  if block_heading is not None:
    return data_structure_builder(cif_model[block_heading], **kwds)
  else:
    xs = None
    for block in cif_model.values():
      try:
        return data_structure_builder(block)
      except:
        continue
