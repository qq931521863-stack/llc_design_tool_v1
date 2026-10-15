"""Passive choke identification from measured complex impedance, in SI units.

Independent implementation from circuit admittance and bounded least squares:
Z = 1 / [1/(Rs+jwL) + 1/Rp + jwCp]. No measured data are bundled or extrapolated.
A small-signal fit is not a saturation, DC-bias, thermal or core-loss model.
"""
import json
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import least_squares


@dataclass(frozen=True)
class ChokeModel:
    rs_ohm: float
    inductance_h: float
    rp_ohm: float
    capacitance_f: float

    def values(self):
        return np.array([self.rs_ohm, self.inductance_h, self.rp_ohm, self.capacitance_f])

    def impedance(self, frequency_hz):
        f = np.asarray(frequency_hz, dtype=float)
        if (not np.isfinite(self.values()).all() or np.any(self.values() <= 0)
                or not np.isfinite(f).all() or np.any(f < 0)):
            raise ValueError('Model parameters must be positive; frequencies finite and nonnegative')
        s = 2j*np.pi*f
        return 1.0 / (1.0/(self.rs_ohm + s*self.inductance_h) + 1.0/self.rp_ohm + s*self.capacitance_f)

    def spice(self):
        return ('.SUBCKT FITTED_CHOKE P N\n'
                f'RS P X {self.rs_ohm:.12g}\nL X N {self.inductance_h:.12g}\n'
                f'RP P N {self.rp_ohm:.12g}\nCP P N {self.capacitance_f:.12g}\n'
                '.ENDS FITTED_CHOKE\n')


@dataclass(frozen=True)
class ImpedanceFit:
    model: ChokeModel
    relative_rms_error: float
    jacobian_condition: float
    frequency_min_hz: float
    frequency_max_hz: float
    evaluations: int
    warnings: tuple[str, ...]

    def to_json(self):
        data = asdict(self)
        if not np.isfinite(data['jacobian_condition']):
            data['jacobian_condition'] = None
        return json.dumps(data, indent=2, allow_nan=False)


def fit_choke_impedance(frequency_hz, impedance_ohm) -> ImpedanceFit:
    f = np.asarray(frequency_hz, dtype=float)
    z = np.asarray(impedance_ohm, dtype=complex)
    if (f.ndim != 1 or z.shape != f.shape or len(f) < 8 or
            not np.isfinite(f).all() or not np.isfinite(z).all() or
            np.any(f <= 0) or np.any(np.diff(f) <= 0) or np.any(abs(z) == 0)):
        raise ValueError('Need >=8 finite complex impedance samples at strictly increasing positive frequencies')
    # Normalize each complex residual, fitting real AND imaginary components.
    # Log-parameters make positivity structural and avoid H/pF scale imbalance.
    scale = np.maximum(abs(z), np.max(abs(z))*1e-9)
    low = np.array([1e-7, 1e-9, 1e-2, 1e-15])
    high = np.array([1e4, 10.0, 1e10, 1e-3])
    inductive = z.imag > 0
    if not np.any(inductive):
        raise ValueError('No inductive samples; this choke model is not identifiable from this band')
    l0 = float(np.median((z.imag/(2*np.pi*f))[inductive][:max(3, len(f)//5)]))
    r0 = max(float(z.real[0]), 1e-5)
    peak = int(np.argmax(abs(z)))
    c0 = 1 / ((2*np.pi*f[peak])**2 * max(l0, 1e-9))
    initial = np.array([r0, l0, max(float(abs(z[peak])), 10), c0])
    loglow, loghigh = np.log(low), np.log(high)

    def residual(log_parameters):
        value = (ChokeModel(*np.exp(log_parameters)).impedance(f) - z)/scale
        return np.concatenate((value.real, value.imag))

    results = []
    for factor in (0.1, 1.0, 10.0):
        start = initial.copy()
        start[3] *= factor
        start = np.clip(start, low*1.01, high/1.01)
        results.append(least_squares(residual, np.log(start), bounds=(loglow, loghigh),
                                     loss='soft_l1', f_scale=0.05, max_nfev=800,
                                     ftol=1e-11, xtol=1e-11, gtol=1e-11))
    successful = [r for r in results if r.success and np.isfinite(r.cost)]
    if not successful:
        raise ValueError('Impedance fit did not converge')
    result = min(successful, key=lambda r: r.cost)
    model = ChokeModel(*map(float, np.exp(result.x)))
    error = float(np.sqrt(np.mean(abs((model.impedance(f)-z)/scale)**2)))
    condition = float(np.linalg.cond(result.jac))
    warnings = []
    if condition > 1e6:
        warnings.append('Parameters weakly identifiable: extend the measured frequency range')
    fr = 1/(2*np.pi*np.sqrt(model.inductance_h*model.capacitance_f))
    if not f[0] < fr < f[-1]:
        warnings.append('Resonance outside measured band: Cp/Rp are not independently validated')
    if np.any(np.minimum(result.x-loglow, loghigh-result.x) < 0.02):
        warnings.append('Fit approaches parameter bounds; inspect model and data units')
    if error > 0.1:
        warnings.append('Relative complex RMS residual exceeds 10%; this topology may be inadequate')
    warnings.append('Valid only over measured band and measurement bias/temperature; no large-signal loss prediction')
    return ImpedanceFit(model, error, condition, float(f[0]), float(f[-1]),
                        sum(r.nfev for r in results), tuple(warnings))


def load_impedance_csv(path):
    """Explicit columns: frequency_hz, real_ohm, imag_ohm. Never infer units."""
    data = np.genfromtxt(path, delimiter=',', names=True, encoding='utf-8-sig')
    fields = ('frequency_hz', 'real_ohm', 'imag_ohm')
    if data.dtype.names is None or any(k not in data.dtype.names for k in fields):
        raise ValueError('CSV headers required: frequency_hz,real_ohm,imag_ohm')
    data = np.atleast_1d(data)
    return data[fields[0]], data[fields[1]] + 1j*data[fields[2]]
