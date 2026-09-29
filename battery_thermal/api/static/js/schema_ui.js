// UI-side catalogue of input fields (labels, units, help). The authoritative schema is engine/schemas.py.

export const CELL_GROUPS = [
  ['Identification', [
    { path: 'cell.name', label: 'Cell name / part no.', type: 'text' },
    { path: 'cell.chemistry', label: 'Chemistry', type: 'text', help: 'e.g. LFP, NMC811, NCA' },
    { path: 'cell.form_factor', label: 'Cell type', type: 'select', options: ['', 'prismatic', 'cylindrical', 'pouch'] },
  ]],
  ['Electrical', [
    { path: 'cell.capacity_ah', label: 'Nominal capacity', unit: 'Ah', required: true },
    { path: 'cell.v_nom', label: 'Nominal voltage', unit: 'V', required: true },
    { path: 'cell.v_max', label: 'Maximum voltage', unit: 'V' },
    { path: 'cell.v_min', label: 'Minimum voltage', unit: 'V' },
  ]],
  ['Resistance', [
    { path: 'cell.r_dc_mohm', label: 'DC internal resistance (DCIR)', unit: 'mΩ', required: true,
      help: 'Needed for Joule heat unless an R-vs-SOC/T table is provided.' },
    { path: 'cell.r_ac_mohm', label: 'AC impedance (1 kHz)', unit: 'mΩ', help: 'Information only - under-estimates DC resistance.' },
    { path: 'cell.r_ref_soc_pct', label: 'Reference SOC of R', unit: '%' },
    { path: 'cell.r_ref_temp_c', label: 'Reference temperature of R', unit: '°C' },
  ]],
  ['C-rate capability', [
    { path: 'cell.max_discharge_c', label: 'Max continuous discharge', unit: 'C' },
    { path: 'cell.max_charge_c', label: 'Max continuous charge', unit: 'C' },
    { path: 'cell.pulse_discharge_c', label: 'Pulse discharge capability', unit: 'C' },
    { path: 'cell.pulse_charge_c', label: 'Pulse charge capability', unit: 'C' },
    { path: 'cell.pulse_duration_s', label: 'Pulse duration', unit: 's' },
  ]],
  ['Geometry & thermal mass', [
    { path: 'cell.length_mm', label: 'Length', unit: 'mm' },
    { path: 'cell.width_mm', label: 'Width', unit: 'mm' },
    { path: 'cell.height_mm', label: 'Height', unit: 'mm' },
    { path: 'cell.diameter_mm', label: 'Diameter (cylindrical)', unit: 'mm' },
    { path: 'cell.mass_kg', label: 'Cell mass', unit: 'kg', required: true },
    { path: 'cell.cp_j_kg_k', label: 'Specific heat capacity', unit: 'J/(kg·K)', required: true,
      help: 'Rarely on datasheets - confirm or accept an assumption.' },
  ]],
  ['Temperature limits', [
    { path: 'cell.t_op_min_c', label: 'Operating min', unit: '°C' },
    { path: 'cell.t_op_max_c', label: 'Operating max', unit: '°C' },
    { path: 'cell.t_charge_min_c', label: 'Charge min', unit: '°C' },
    { path: 'cell.t_charge_max_c', label: 'Charge max', unit: '°C' },
    { path: 'cell.t_rec_min_c', label: 'Recommended min', unit: '°C' },
    { path: 'cell.t_rec_max_c', label: 'Recommended max', unit: '°C' },
  ]],
];

export const CELL_CURVES = [
  { key: 'r_vs_soc', label: 'Resistance vs SOC', xl: 'SOC [%]', yl: 'R [mΩ]' },
  { key: 'r_vs_temp', label: 'Resistance vs temperature', xl: 'T [°C]', yl: 'R [mΩ]' },
  { key: 'ocv_vs_soc', label: 'OCV vs SOC', xl: 'SOC [%]', yl: 'OCV [V]' },
  { key: 'dudt_vs_soc', label: 'Entropic coefficient dU/dT vs SOC', xl: 'SOC [%]', yl: 'dU/dT [mV/K]' },
  { key: 'capacity_vs_temp', label: 'Capacity vs temperature', xl: 'T [°C]', yl: 'Capacity [% of nominal]' },
];
export const CELL_MAPS = [
  { key: 'r_map', label: 'Resistance map R(SOC,T)', zl: 'R [mΩ]' },
  { key: 'ocv_map', label: 'OCV map OCV(SOC,T)', zl: 'OCV [V]' },
];

export const PACK_FIELDS = [
  { path: 'pack.ns', label: 'Cells in series (Ns)', required: true, step: 1, min: 1 },
  { path: 'pack.np', label: 'Cells in parallel (Np)', required: true, step: 1, min: 1 },
  { path: 'pack.n_modules', label: 'Number of modules', required: true, step: 1, min: 1 },
  { path: 'pack.cells_per_module', label: 'Cells per module', required: true, step: 1, min: 1 },
  { path: 'pack.module_arrangement', label: 'Module arrangement', type: 'select',
    options: [{ v: 'series', l: 'Modules in series' }, { v: 'parallel', l: 'Modules in parallel' },
              { v: 'series_parallel', l: 'Series-parallel' }] },
  { path: 'pack.modules_in_series', label: 'Modules in series (series-parallel only)', step: 1, min: 1 },
];
export const PACK_STATED = [
  { path: 'pack.pack_nominal_voltage_v', label: 'Pack nominal voltage (stated)', unit: 'V', help: 'Optional - used for consistency check.' },
  { path: 'pack.pack_capacity_ah', label: 'Pack capacity (stated)', unit: 'Ah', help: 'Optional - consistency check.' },
  { path: 'pack.pack_energy_kwh', label: 'Pack energy (stated)', unit: 'kWh', help: 'Optional - consistency check.' },
];
export const PACK_OPERATING = [
  { path: 'pack.soc_initial_pct', label: 'Initial SOC', unit: '%' },
  { path: 'pack.soc_min_pct', label: 'Minimum SOC', unit: '%' },
  { path: 'pack.soc_max_pct', label: 'Maximum SOC', unit: '%' },
  { path: 'pack.t_initial_c', label: 'Initial battery temperature', unit: '°C' },
  { path: 'pack.t_target_max_c', label: 'Target max cell temperature', unit: '°C' },
  { path: 'pack.target_delta_t_k', label: 'Target cell-to-cell ΔT', unit: 'K' },
  { path: 'pack.t_ambient_c', label: 'Ambient temperature', unit: '°C' },
];

export const REQUIRED_CELL = ['cell.capacity_ah', 'cell.v_nom', 'cell.mass_kg', 'cell.cp_j_kg_k'];
