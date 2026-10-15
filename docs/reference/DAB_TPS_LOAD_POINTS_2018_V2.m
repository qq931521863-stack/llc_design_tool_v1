function DAB_TPS_LOAD_POINTS_2018_V2()
% DAB_TPS_LOAD_POINTS_2018_V2
%
% MATLAB R2018a/R2018b compatible.
%
% Revised design:
%   VA                   = 400 V
%   VB                   = 300, 350, 400, 450, 500 V
%   Transformer ratio    = 1:1
%   Switching frequency  = 80 kHz
%   DAB inductance       = 30 uH
%   Output capacitor     = 1000 uF
%   Primary series Cp    = 4.7 uF
%   Secondary series C   = removed
%
% Load points:
%   10%, 30%, 50%, 75%, 100%
%
% Full-load power:
%   VB <= 350 V          5 kW
%   350 V < VB < 400 V   linear 5 kW to 7 kW
%   VB >= 400 V          7 kW
%
% This file calculates and plots:
%
%   A. TPS control variables
%      - m = n*VA/VB
%      - Pbase = VA*VB/(8*n*fs*L)
%      - p = Pref/Pbase
%      - power headroom
%      - TPS mode boundary
%      - D1, D2, D3
%      - D1, D2, D3 electrical angles
%
%   B. DAB electrical waveforms
%      - primary H4 bridge voltage vA
%      - secondary H4 bridge voltage vB referred to primary
%      - DAB transfer-inductor voltage vL
%      - DAB transfer-inductor current iL
%
%   C. Current stress
%      - iL RMS
%      - iL peak
%      - iL peak-to-peak
%      - average absolute iL
%      - crest factor
%
%   D. Power-quality and circulation metrics
%      - reconstructed transferred power
%      - primary backflow power
%      - secondary backflow power
%      - circulation-power proxy
%
%   E. Capacitor stress
%      - primary 4.7-uF capacitor RMS/peak voltage
%      - primary capacitor RMS current
%      - output-capacitor RMS ripple current
%      - output-capacitor switching ripple voltage
%
%   F. Commutation and PWM margins
%      - minimum primary-leg commutation current magnitude
%      - minimum secondary-leg commutation current magnitude
%      - minimum all-leg commutation current magnitude
%      - leakage-inductor energy at minimum commutation current
%      - primary active pulse width
%      - secondary active pulse width
%      - PWM counts at 100-MHz TBCLK
%
% Important:
%   The TPS command law is still based on the ideal VA/VB/L/fs power model.
%   The primary series capacitor is evaluated as a plant effect; it does not
%   silently modify the TPS closed-form equations.
%
% Run:
%   DAB_TPS_LOAD_POINTS_2018_V2
%
% Output directory:
%   DAB_TPS_LOAD_POINT_RESULTS_V2

clc;
close all;

cfg = default_configuration();

if exist(cfg.output_dir, 'dir') == 0
    mkdir(cfg.output_dir);
end

print_configuration(cfg);

result = calculate_all_operating_points(cfg);

plot_phase_angles(result, cfg);
plot_power_utilization(result, cfg);
plot_current_stress(result, cfg);
plot_power_tracking(result, cfg);
plot_backflow_power(result, cfg);
plot_primary_capacitor_stress(result, cfg);
plot_output_capacitor_stress(result, cfg);
plot_commutation_margin(result, cfg);
plot_pulse_widths(result, cfg);
plot_all_waveform_cases(result, cfg);

write_result_csv(result, cfg);
print_operating_point_table(result, cfg);

fprintf('\nCompleted.\n');
fprintf('Result directory: %s\n\n', cfg.output_dir);

end


%==========================================================================
% Configuration
%==========================================================================
function cfg = default_configuration()

cfg.VA_V = 400.0;

cfg.VB_cases_V = [300.0 350.0 400.0 450.0 500.0];
cfg.load_ratio = [0.10 0.30 0.50 0.75 1.00];

cfg.n = 1.0;
cfg.fs_Hz = 80e3;
cfg.Ts_s = 1.0 / cfg.fs_Hz;

% Revised from 25 uH to 30 uH.
cfg.L_H = 30e-6;

cfg.Co_F = 1000e-6;

% Primary series capacitor only.
cfg.Cprimary_F = 4.7e-6;

% Primary-referred lumped damping resistance.
% Replace with the measured total HF series resistance when available.
cfg.Rseries_ohm = 20e-3;

cfg.Plow_W = 5000.0;
cfg.Prated_W = 7000.0;
cfg.Vderate_end_V = 350.0;
cfg.Vrated_start_V = 400.0;

% Keep a small numerical margin from the p = 1 singular endpoint.
cfg.p_limit = 0.995;

cfg.wave_samples = 16384;

cfg.TBCLK_Hz = 100e6;
cfg.TBPRD = round(cfg.TBCLK_Hz / cfg.fs_Hz);
cfg.half_TBPRD = cfg.TBPRD / 2.0;

cfg.output_dir = 'DAB_TPS_LOAD_POINT_RESULTS_V2';
cfg.save_figures = 1;

end


%==========================================================================
% Calculate every VB/load operating point
%==========================================================================
function result = calculate_all_operating_points(cfg)

number_of_VB = length(cfg.VB_cases_V);
number_of_loads = length(cfg.load_ratio);
Npoint = number_of_VB * number_of_loads;

fields = { ...
    'VB_V', ...
    'load_percent', ...
    'P100_W', ...
    'Pref_W', ...
    'Iout_command_A', ...
    'm', ...
    'Pbase_W', ...
    'p_raw', ...
    'p', ...
    'p_headroom', ...
    'P_headroom_W', ...
    'p_boundary', ...
    'distance_to_boundary', ...
    'feasible', ...
    'mode_id', ...
    'D1', ...
    'D2', ...
    'D3', ...
    'D1_deg', ...
    'D2_deg', ...
    'D3_deg', ...
    'secondary_inner_deg', ...
    'D1_count', ...
    'D2_count', ...
    'D3_count', ...
    'primary_active_width_us', ...
    'secondary_active_width_us', ...
    'primary_active_width_count', ...
    'secondary_active_width_count', ...
    'P_Lonly_W', ...
    'P_with_Cp_W', ...
    'power_error_with_Cp_percent', ...
    'Irms_Lonly_A', ...
    'Ipk_Lonly_A', ...
    'Irms_with_Cp_A', ...
    'Ipk_with_Cp_A', ...
    'Ipp_with_Cp_A', ...
    'Iabsavg_with_Cp_A', ...
    'current_crest_factor', ...
    'Pback_primary_W', ...
    'Pback_secondary_W', ...
    'Pcirculation_proxy_W', ...
    'Pback_primary_ratio', ...
    'Pback_secondary_ratio', ...
    'VCp_rms_V', ...
    'VCp_peak_V', ...
    'ICp_rms_A', ...
    'Iin_dc_avg_A', ...
    'Iin_ripple_rms_A', ...
    'Iout_actual_avg_A', ...
    'Iout_ripple_rms_A', ...
    'Vout_ripple_pp_V', ...
    'Icomm_primary_min_abs_A', ...
    'Icomm_secondary_min_abs_A', ...
    'Icomm_all_min_abs_A', ...
    'Ecomm_min_uJ', ...
    'Vrms_primary_bridge_V', ...
    'Vrms_secondary_referred_V', ...
    'transformer_VA_primary', ...
    'transformer_VA_secondary_ref', ...
    'max_di_dt_A_per_us'};

for k = 1:length(fields)
    result.(fields{k}) = zeros(Npoint, 1);
end

result.waveform = cell(Npoint, 1);

point = 0;

for voltage_index = 1:number_of_VB
    VB = cfg.VB_cases_V(voltage_index);
    P100 = full_load_power_limit(VB, cfg);

    for load_index = 1:number_of_loads
        point = point + 1;

        load_ratio = cfg.load_ratio(load_index);
        Pref = P100 * load_ratio;

        [D1, D2, D3, status] = ...
            dab_tps_control_from_physical_power( ...
                cfg.VA_V, ...
                VB, ...
                Pref, ...
                cfg.n, ...
                cfg.fs_Hz, ...
                cfg.L_H, ...
                cfg.p_limit);

        metric_L = periodic_dab_model( ...
            cfg.VA_V, VB, cfg.n, ...
            D1, D2, D3, ...
            cfg, 0);

        metric_Cp = periodic_dab_model( ...
            cfg.VA_V, VB, cfg.n, ...
            D1, D2, D3, ...
            cfg, 1);

        result.VB_V(point) = VB;
        result.load_percent(point) = 100.0 * load_ratio;
        result.P100_W(point) = P100;
        result.Pref_W(point) = Pref;
        result.Iout_command_A(point) = Pref / VB;

        result.m(point) = status.m;
        result.Pbase_W(point) = status.Pbase_W;
        result.p_raw(point) = status.p_raw;
        result.p(point) = status.p;
        result.p_headroom(point) = 1.0 - status.p_raw;
        result.P_headroom_W(point) = status.Pbase_W - Pref;
        result.p_boundary(point) = status.p_boundary;
        result.distance_to_boundary(point) = ...
            status.p - status.p_boundary;
        result.feasible(point) = status.feasible;
        result.mode_id(point) = status.mode_id;

        result.D1(point) = D1;
        result.D2(point) = D2;
        result.D3(point) = D3;

        result.D1_deg(point) = 180.0 * D1;
        result.D2_deg(point) = 180.0 * D2;
        result.D3_deg(point) = 180.0 * D3;
        result.secondary_inner_deg(point) = 180.0 * (D3 - D2);

        result.D1_count(point) = round(D1 * cfg.half_TBPRD);
        result.D2_count(point) = round(D2 * cfg.half_TBPRD);
        result.D3_count(point) = round(D3 * cfg.half_TBPRD);

        result.primary_active_width_us(point) = ...
            (1.0 - D1) * cfg.Ts_s * 0.5 * 1e6;

        result.secondary_active_width_us(point) = ...
            (1.0 - (D3 - D2)) * cfg.Ts_s * 0.5 * 1e6;

        result.primary_active_width_count(point) = ...
            round((1.0 - D1) * cfg.half_TBPRD);

        result.secondary_active_width_count(point) = ...
            round((1.0 - (D3 - D2)) * cfg.half_TBPRD);

        result.P_Lonly_W(point) = metric_L.Pout_W;
        result.P_with_Cp_W(point) = metric_Cp.Pout_W;

        result.power_error_with_Cp_percent(point) = ...
            100.0 * (metric_Cp.Pout_W - Pref) / max(Pref, 1e-12);

        result.Irms_Lonly_A(point) = metric_L.Irms_A;
        result.Ipk_Lonly_A(point) = metric_L.Ipk_A;

        result.Irms_with_Cp_A(point) = metric_Cp.Irms_A;
        result.Ipk_with_Cp_A(point) = metric_Cp.Ipk_A;
        result.Ipp_with_Cp_A(point) = metric_Cp.Ipp_A;
        result.Iabsavg_with_Cp_A(point) = metric_Cp.Iabsavg_A;
        result.current_crest_factor(point) = metric_Cp.crest_factor;

        result.Pback_primary_W(point) = metric_Cp.Pback_primary_W;
        result.Pback_secondary_W(point) = metric_Cp.Pback_secondary_W;
        result.Pcirculation_proxy_W(point) = ...
            metric_Cp.Pcirculation_proxy_W;

        result.Pback_primary_ratio(point) = ...
            metric_Cp.Pback_primary_W / ...
            max(abs(metric_Cp.Pout_W), 1e-12);

        result.Pback_secondary_ratio(point) = ...
            metric_Cp.Pback_secondary_W / ...
            max(abs(metric_Cp.Pout_W), 1e-12);

        result.VCp_rms_V(point) = metric_Cp.VCp_rms_V;
        result.VCp_peak_V(point) = metric_Cp.VCp_peak_V;
        result.ICp_rms_A(point) = metric_Cp.Irms_A;

        result.Iin_dc_avg_A(point) = metric_Cp.Iin_dc_avg_A;
        result.Iin_ripple_rms_A(point) = metric_Cp.Iin_ripple_rms_A;

        result.Iout_actual_avg_A(point) = ...
            metric_Cp.Iout_dc_avg_A;

        result.Iout_ripple_rms_A(point) = ...
            metric_Cp.Iout_ripple_rms_A;

        result.Vout_ripple_pp_V(point) = ...
            metric_Cp.Vout_ripple_pp_V;

        result.Icomm_primary_min_abs_A(point) = ...
            metric_Cp.Icomm_primary_min_abs_A;

        result.Icomm_secondary_min_abs_A(point) = ...
            metric_Cp.Icomm_secondary_min_abs_A;

        result.Icomm_all_min_abs_A(point) = ...
            metric_Cp.Icomm_all_min_abs_A;

        result.Ecomm_min_uJ(point) = ...
            0.5 * cfg.L_H * ...
            metric_Cp.Icomm_all_min_abs_A^2 * 1e6;

        result.Vrms_primary_bridge_V(point) = ...
            metric_Cp.Vrms_primary_bridge_V;

        result.Vrms_secondary_referred_V(point) = ...
            metric_Cp.Vrms_secondary_referred_V;

        result.transformer_VA_primary(point) = ...
            metric_Cp.Vrms_primary_bridge_V * metric_Cp.Irms_A;

        result.transformer_VA_secondary_ref(point) = ...
            metric_Cp.Vrms_secondary_referred_V * metric_Cp.Irms_A;

        result.max_di_dt_A_per_us(point) = ...
            metric_Cp.max_di_dt_A_per_us;

        result.waveform{point} = metric_Cp;
    end
end

end


%==========================================================================
% Physical-power TPS controller
%==========================================================================
function [D1, D2, D3, status] = ...
    dab_tps_control_from_physical_power( ...
        VA, VB, Pref, n, fs, L, p_limit)

status.m = n * VA / VB;
status.Pbase_W = VA * VB / (8.0 * n * fs * L);
status.p_raw = Pref / status.Pbase_W;

if status.p_raw <= 1.0
    status.feasible = 1;
else
    status.feasible = 0;
end

status.p = min(max(status.p_raw, 0.0), p_limit);

[D1, D2, D3, mode_id, p_boundary] = ...
    mcso_tps_phase_equations(status.p, status.m);

status.mode_id = mode_id;
status.p_boundary = p_boundary;

end


%==========================================================================
% Existing TPS/MCSO closed-form equations
%==========================================================================
function [D1, D2, D3, mode_id, p_boundary] = ...
    mcso_tps_phase_equations(p, m)

p = min(max(p, 0.0), 1.0);

if abs(m - 1.0) < 1e-12
    p_boundary = 0.0;

    D1 = 0.0;
    D2 = 0.5 * (1.0 - sqrt(max(0.0, 1.0 - p)));
    D3 = D2;

    mode_id = 2;
    return;
end

if m > 1.0
    p_boundary = 2.0 * (m - 1.0) / (m * m);

    if p < p_boundary
        D1 = 1.0 - sqrt(p / (2.0 * (m - 1.0)));
        D2 = (m - 1.0) * (1.0 - D1);
        D3 = D1;
        mode_id = 3;
    else
        D1 = (m - 1.0) * ...
            sqrt((1.0 - p) / ...
            (m * m - 2.0 * m + 2.0));

        D2 = 0.5 + ...
            D1 * (m - 2.0) / ...
            (2.0 * (m - 1.0));

        D3 = D2;
        mode_id = 4;
    end
else
    p_boundary = 2.0 * (m - m * m);

    if p < p_boundary
        D1 = 1.0 - ...
            sqrt(p / (2.0 * m * (1.0 - m)));

        D2 = 0.0;
        D3 = m * D1 - m + 1.0;
        mode_id = 1;
    else
        D1 = 0.0;

        D2 = 0.5 * ...
            (1.0 - ...
            sqrt((1.0 - p) / ...
            (2.0 * m * m - 2.0 * m + 1.0)));

        D3 = 2.0 * m * D2 - D2 - m + 1.0;
        mode_id = 2;
    end
end

D1 = min(max(D1, 0.0), 1.0);
D2 = min(max(D2, 0.0), 1.0);
D3 = min(max(D3, 0.0), 1.0);

end


%==========================================================================
% Full-load power profile
%==========================================================================
function P100 = full_load_power_limit(VB, cfg)

if VB <= cfg.Vderate_end_V
    P100 = cfg.Plow_W;
elseif VB < cfg.Vrated_start_V
    ratio = ...
        (VB - cfg.Vderate_end_V) / ...
        (cfg.Vrated_start_V - cfg.Vderate_end_V);

    P100 = cfg.Plow_W + ...
        ratio * (cfg.Prated_W - cfg.Plow_W);
else
    P100 = cfg.Prated_W;
end

end


%==========================================================================
% Periodic DAB model
%==========================================================================
function metric = periodic_dab_model( ...
    VA, VB, n, D1, D2, D3, cfg, include_primary_capacitor)

N = cfg.wave_samples;

t_pu = (0:(N - 1)) / N;
t_s = t_pu * cfg.Ts_s;

qA1 = bridge_leg_square(t_pu, 0.0);
qA2 = bridge_leg_square(t_pu, 0.5 * D1);

qB1 = bridge_leg_square(t_pu, 0.5 * D2);
qB2 = bridge_leg_square(t_pu, 0.5 * D3);

sA = 0.5 * (qA1 + qA2);
sB = 0.5 * (qB1 + qB2);

vA_V = VA * sA;
vB_referred_V = (VB / n) * sB;

vDrive_V = vA_V - vB_referred_V;

if include_primary_capacitor == 0
    dt_s = cfg.Ts_s / N;

    iL_A = cumsum(vDrive_V) * dt_s / cfg.L_H;
    iL_A = iL_A - mean(iL_A);

    vCp_V = zeros(size(iL_A));
else
    [iL_A, vCp_V] = ...
        solve_periodic_LC_current(vDrive_V, cfg);
end

% Actual inductor voltage after primary series capacitor.
vL_V = vDrive_V - vCp_V - cfg.Rseries_ohm * iL_A;

p_primary_W = vA_V .* iL_A;
p_secondary_W = vB_referred_V .* iL_A;

% DC-side bridge currents.
iin_dc_A = sA .* iL_A;
iout_dc_A = sB .* iL_A / n;

iin_dc_avg_A = mean(iin_dc_A);
iout_dc_avg_A = mean(iout_dc_A);

iin_ripple_A = iin_dc_A - iin_dc_avg_A;
iout_ripple_A = iout_dc_A - iout_dc_avg_A;

% Output capacitor switching ripple.
dt_s = cfg.Ts_s / N;
vout_ripple_V = cumsum(iout_ripple_A) * dt_s / cfg.Co_F;
vout_ripple_V = vout_ripple_V - mean(vout_ripple_V);

% Switching/commutation currents at all leg transitions.
primary_events = [0.0, 0.5, ...
                  0.5 * D1, mod(0.5 * D1 + 0.5, 1.0)];

secondary_events = [0.5 * D2, mod(0.5 * D2 + 0.5, 1.0), ...
                    0.5 * D3, mod(0.5 * D3 + 0.5, 1.0)];

Icomm_primary_A = sample_periodic_waveform(iL_A, primary_events);
Icomm_secondary_A = sample_periodic_waveform(iL_A, secondary_events);

metric.t_pu = t_pu;
metric.t_s = t_s;

metric.vA_V = vA_V;
metric.vB_referred_V = vB_referred_V;
metric.vDrive_V = vDrive_V;
metric.vL_V = vL_V;

metric.iL_A = iL_A;
metric.vCp_V = vCp_V;

metric.iin_dc_A = iin_dc_A;
metric.iout_dc_A = iout_dc_A;
metric.iout_ripple_A = iout_ripple_A;
metric.vout_ripple_V = vout_ripple_V;

metric.Pin_W = mean(p_primary_W);
metric.Pout_W = mean(p_secondary_W);

metric.Irms_A = sqrt(mean(iL_A .* iL_A));
metric.Ipk_A = max(abs(iL_A));
metric.Ipp_A = max(iL_A) - min(iL_A);
metric.Iabsavg_A = mean(abs(iL_A));

metric.crest_factor = ...
    metric.Ipk_A / max(metric.Irms_A, 1e-12);

metric.Pback_primary_W = ...
    mean(max(-p_primary_W, 0.0));

metric.Pback_secondary_W = ...
    mean(max(-p_secondary_W, 0.0));

% This is a circulation/loss proxy, not IEEE reactive power.
metric.Pcirculation_proxy_W = ...
    mean(abs(p_primary_W)) - abs(mean(p_primary_W));

metric.VCp_rms_V = sqrt(mean(vCp_V .* vCp_V));
metric.VCp_peak_V = max(abs(vCp_V));

metric.Iin_dc_avg_A = iin_dc_avg_A;
metric.Iin_ripple_rms_A = ...
    sqrt(mean(iin_ripple_A .* iin_ripple_A));

metric.Iout_dc_avg_A = iout_dc_avg_A;
metric.Iout_ripple_rms_A = ...
    sqrt(mean(iout_ripple_A .* iout_ripple_A));

metric.Vout_ripple_pp_V = ...
    max(vout_ripple_V) - min(vout_ripple_V);

metric.Icomm_primary_A = Icomm_primary_A;
metric.Icomm_secondary_A = Icomm_secondary_A;

metric.Icomm_primary_min_abs_A = ...
    min(abs(Icomm_primary_A));

metric.Icomm_secondary_min_abs_A = ...
    min(abs(Icomm_secondary_A));

metric.Icomm_all_min_abs_A = ...
    min([abs(Icomm_primary_A), abs(Icomm_secondary_A)]);

metric.Vrms_primary_bridge_V = ...
    sqrt(mean(vA_V .* vA_V));

metric.Vrms_secondary_referred_V = ...
    sqrt(mean(vB_referred_V .* vB_referred_V));

metric.max_di_dt_A_per_us = ...
    max(abs(vL_V / cfg.L_H)) / 1e6;

end


%==========================================================================
% Periodic L + primary capacitor solution
%==========================================================================
function [iL_A, vCp_V] = ...
    solve_periodic_LC_current(vDrive_V, cfg)

N = length(vDrive_V);

Vh = fft(vDrive_V) / N;

harmonic_number = [0:(N / 2), (-N / 2 + 1):-1];
omega = 2.0 * pi * cfg.fs_Hz * harmonic_number;

Ih = zeros(1, N);
VCh = zeros(1, N);

for index = 1:N
    if harmonic_number(index) == 0
        Ih(index) = 0.0;
        VCh(index) = 0.0;
    else
        ZL = 1i * omega(index) * cfg.L_H;
        ZC = 1.0 / ...
            (1i * omega(index) * cfg.Cprimary_F);

        Ztotal = cfg.Rseries_ohm + ZL + ZC;

        Ih(index) = Vh(index) / Ztotal;
        VCh(index) = Ih(index) * ZC;
    end
end

iL_A = real(ifft(Ih * N));
vCp_V = real(ifft(VCh * N));

end


%==========================================================================
% Sample periodic waveform at normalized event times
%==========================================================================
function values = sample_periodic_waveform(waveform, event_time_pu)

N = length(waveform);
values = zeros(size(event_time_pu));

for k = 1:length(event_time_pu)
    normalized_time = mod(event_time_pu(k), 1.0);
    index = floor(normalized_time * N) + 1;

    if index > N
        index = 1;
    end

    values(k) = waveform(index);
end

end


%==========================================================================
% Bridge-leg square wave
%==========================================================================
function q = bridge_leg_square(t_pu, phase_cycles)

local_time = mod(t_pu - phase_cycles, 1.0);

q = ones(size(local_time));
q(local_time >= 0.5) = -1.0;

end


%==========================================================================
% Plot helpers
%==========================================================================
function plot_phase_angles(result, cfg)

figure('Name', 'TPS phase angles');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.D1_deg(mask), '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.D2_deg(mask), '-s', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.D3_deg(mask), '-^', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.secondary_inner_deg(mask), ...
         '--', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Angle (degree)');
    title(sprintf('VB = %.0f V, m = %.4f', ...
          VB, cfg.n * cfg.VA_V / VB));
    legend('D1', 'D2', 'D3 abs', 'D3-D2', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '01_D1_D2_D3_angles.png');

end


function plot_power_utilization(result, cfg)

figure('Name', 'Power utilization and mode boundary');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.p(mask), '-o', 'LineWidth', 1.3);
    hold on;
    plot(result.load_percent(mask), ...
         result.p_boundary(mask), '--', 'LineWidth', 1.2);
    plot([0 100], [1 1], ':', 'LineWidth', 1.0);
    hold off;

    xlabel('Load (%)');
    ylabel('Normalized power');
    title(sprintf('VB = %.0f V', VB));
    legend('p used', 'TPS boundary', 'p=1 limit', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '02_power_utilization.png');

end


function plot_current_stress(result, cfg)

figure('Name', 'Inductor current stress');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.Irms_with_Cp_A(mask), ...
         '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.Ipk_with_Cp_A(mask), ...
         '-s', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.Ipp_with_Cp_A(mask), ...
         '-^', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Current (A)');
    title(sprintf('VB = %.0f V', VB));
    legend('IL RMS', 'IL peak', 'IL p-p', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '03_current_stress.png');

end


function plot_power_tracking(result, cfg)

figure('Name', 'Power tracking');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.Pref_W(mask) / 1000.0, ...
         '-o', 'LineWidth', 1.3);
    hold on;
    plot(result.load_percent(mask), ...
         result.P_Lonly_W(mask) / 1000.0, ...
         '--', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.P_with_Cp_W(mask) / 1000.0, ...
         '-s', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Power (kW)');
    title(sprintf('VB = %.0f V', VB));
    legend('Command', 'L-only', 'L + primary Cp', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '04_power_tracking.png');

end


function plot_backflow_power(result, cfg)

figure('Name', 'Backflow and circulation power');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.Pback_primary_W(mask) / 1000.0, ...
         '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.Pback_secondary_W(mask) / 1000.0, ...
         '-s', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.Pcirculation_proxy_W(mask) / 1000.0, ...
         '--', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Power (kW)');
    title(sprintf('VB = %.0f V', VB));
    legend('Primary backflow', ...
           'Secondary backflow', ...
           'Circulation proxy', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '05_backflow_power.png');

end


function plot_primary_capacitor_stress(result, cfg)

figure('Name', 'Primary series capacitor stress');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    yyaxis left;
    plot(result.load_percent(mask), ...
         result.VCp_rms_V(mask), ...
         '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.VCp_peak_V(mask), ...
         '-s', 'LineWidth', 1.2);
    plot([0 100], [250 250], ':', 'LineWidth', 1.0);
    hold off;
    ylabel('Capacitor voltage (V)');

    yyaxis right;
    plot(result.load_percent(mask), ...
         result.ICp_rms_A(mask), ...
         '--', 'LineWidth', 1.2);
    ylabel('Capacitor RMS current (A)');

    xlabel('Load (%)');
    title(sprintf('VB = %.0f V', VB));
    grid on;
end

save_figure(cfg, '06_primary_capacitor_stress.png');

end


function plot_output_capacitor_stress(result, cfg)

figure('Name', 'Output capacitor stress');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    yyaxis left;
    plot(result.load_percent(mask), ...
         result.Iout_ripple_rms_A(mask), ...
         '-o', 'LineWidth', 1.2);
    ylabel('Co RMS ripple current (A)');

    yyaxis right;
    plot(result.load_percent(mask), ...
         result.Vout_ripple_pp_V(mask), ...
         '-s', 'LineWidth', 1.2);
    ylabel('Output ripple p-p (V)');

    xlabel('Load (%)');
    title(sprintf('VB = %.0f V', VB));
    grid on;
end

save_figure(cfg, '07_output_capacitor_stress.png');

end


function plot_commutation_margin(result, cfg)

figure('Name', 'Commutation-current margin');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.Icomm_primary_min_abs_A(mask), ...
         '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.Icomm_secondary_min_abs_A(mask), ...
         '-s', 'LineWidth', 1.2);
    plot(result.load_percent(mask), ...
         result.Icomm_all_min_abs_A(mask), ...
         '--', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Minimum |Icomm| (A)');
    title(sprintf('VB = %.0f V', VB));
    legend('Primary legs', ...
           'Secondary legs', ...
           'All legs minimum', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '08_commutation_margin.png');

end


function plot_pulse_widths(result, cfg)

figure('Name', 'Active pulse widths');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);
    mask = abs(result.VB_V - VB) < 1e-9;

    subplot(3, 2, voltage_index);

    plot(result.load_percent(mask), ...
         result.primary_active_width_us(mask), ...
         '-o', 'LineWidth', 1.2);
    hold on;
    plot(result.load_percent(mask), ...
         result.secondary_active_width_us(mask), ...
         '-s', 'LineWidth', 1.2);
    hold off;

    xlabel('Load (%)');
    ylabel('Active pulse width (us)');
    title(sprintf('VB = %.0f V', VB));
    legend('Primary bridge', ...
           'Secondary bridge', ...
           'Location', 'best');
    grid on;
end

save_figure(cfg, '09_active_pulse_widths.png');

end


%==========================================================================
% Five VA/VB/IL waveforms for each output voltage
%==========================================================================
function plot_all_waveform_cases(result, cfg)

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);

    figure('Name', sprintf('VA VB IL waveforms VB %.0f V', VB));

    for load_index = 1:length(cfg.load_ratio)
        load_percent = 100.0 * cfg.load_ratio(load_index);

        point = find( ...
            (abs(result.VB_V - VB) < 1e-9) & ...
            (abs(result.load_percent - load_percent) < 1e-9), ...
            1, 'first');

        wave = result.waveform{point};

        subplot(5, 1, load_index);

        yyaxis left;
        stairs(wave.t_s * 1e6, ...
               wave.vA_V, ...
               'LineWidth', 1.0);
        hold on;
        stairs(wave.t_s * 1e6, ...
               wave.vB_referred_V, ...
               'LineWidth', 1.0);
        hold off;
        ylabel('vA, vB (V)');

        yyaxis right;
        plot(wave.t_s * 1e6, ...
             wave.iL_A, ...
             'LineWidth', 1.1);
        ylabel('iL (A)');

        title(sprintf( ...
            ['Load %.0f%%, %.2f kW, p %.3f, M%u, ' ...
             'D1 %.2f deg, D2 %.2f deg, D3 %.2f deg'], ...
            result.load_percent(point), ...
            result.Pref_W(point) / 1000.0, ...
            result.p(point), ...
            result.mode_id(point), ...
            result.D1_deg(point), ...
            result.D2_deg(point), ...
            result.D3_deg(point)));

        grid on;

        if load_index == 1
            legend('vA primary H4', ...
                   'vB secondary H4 referred', ...
                   'iL', ...
                   'Location', 'best');
        end

        if load_index == length(cfg.load_ratio)
            xlabel('Time (us)');
        end
    end

    filename = sprintf( ...
        '%02u_waveforms_VB_%03.0fV.png', ...
        10 + voltage_index, VB);

    save_figure(cfg, filename);
end

end


%==========================================================================
% CSV output
%==========================================================================
function write_result_csv(result, cfg)

result_table = table( ...
    result.VB_V, ...
    result.load_percent, ...
    result.P100_W, ...
    result.Pref_W, ...
    result.Iout_command_A, ...
    result.m, ...
    result.Pbase_W, ...
    result.p_raw, ...
    result.p, ...
    result.p_headroom, ...
    result.P_headroom_W, ...
    result.p_boundary, ...
    result.distance_to_boundary, ...
    result.feasible, ...
    result.mode_id, ...
    result.D1, ...
    result.D2, ...
    result.D3, ...
    result.D1_deg, ...
    result.D2_deg, ...
    result.D3_deg, ...
    result.secondary_inner_deg, ...
    result.D1_count, ...
    result.D2_count, ...
    result.D3_count, ...
    result.primary_active_width_us, ...
    result.secondary_active_width_us, ...
    result.primary_active_width_count, ...
    result.secondary_active_width_count, ...
    result.P_Lonly_W, ...
    result.P_with_Cp_W, ...
    result.power_error_with_Cp_percent, ...
    result.Irms_Lonly_A, ...
    result.Ipk_Lonly_A, ...
    result.Irms_with_Cp_A, ...
    result.Ipk_with_Cp_A, ...
    result.Ipp_with_Cp_A, ...
    result.Iabsavg_with_Cp_A, ...
    result.current_crest_factor, ...
    result.Pback_primary_W, ...
    result.Pback_secondary_W, ...
    result.Pcirculation_proxy_W, ...
    result.Pback_primary_ratio, ...
    result.Pback_secondary_ratio, ...
    result.VCp_rms_V, ...
    result.VCp_peak_V, ...
    result.ICp_rms_A, ...
    result.Iin_dc_avg_A, ...
    result.Iin_ripple_rms_A, ...
    result.Iout_actual_avg_A, ...
    result.Iout_ripple_rms_A, ...
    result.Vout_ripple_pp_V, ...
    result.Icomm_primary_min_abs_A, ...
    result.Icomm_secondary_min_abs_A, ...
    result.Icomm_all_min_abs_A, ...
    result.Ecomm_min_uJ, ...
    result.Vrms_primary_bridge_V, ...
    result.Vrms_secondary_referred_V, ...
    result.transformer_VA_primary, ...
    result.transformer_VA_secondary_ref, ...
    result.max_di_dt_A_per_us, ...
    'VariableNames', { ...
    'VB_V', ...
    'load_percent', ...
    'P100_W', ...
    'Pref_W', ...
    'Iout_command_A', ...
    'm_nVA_over_VB', ...
    'Pbase_W', ...
    'p_raw', ...
    'p_used', ...
    'p_headroom', ...
    'P_headroom_W', ...
    'p_boundary', ...
    'distance_to_boundary', ...
    'feasible', ...
    'mode_id', ...
    'D1', ...
    'D2', ...
    'D3', ...
    'D1_deg', ...
    'D2_deg', ...
    'D3_deg', ...
    'secondary_inner_deg', ...
    'D1_count', ...
    'D2_count', ...
    'D3_count', ...
    'primary_active_width_us', ...
    'secondary_active_width_us', ...
    'primary_active_width_count', ...
    'secondary_active_width_count', ...
    'P_Lonly_W', ...
    'P_with_primary_Cp_W', ...
    'power_error_with_Cp_percent', ...
    'Irms_Lonly_A', ...
    'Ipk_Lonly_A', ...
    'Irms_with_primary_Cp_A', ...
    'Ipk_with_primary_Cp_A', ...
    'Ipp_with_primary_Cp_A', ...
    'Iabsavg_with_primary_Cp_A', ...
    'current_crest_factor', ...
    'Pback_primary_W', ...
    'Pback_secondary_W', ...
    'Pcirculation_proxy_W', ...
    'Pback_primary_ratio', ...
    'Pback_secondary_ratio', ...
    'VCp_rms_V', ...
    'VCp_peak_V', ...
    'ICp_rms_A', ...
    'Iin_dc_avg_A', ...
    'Iin_ripple_rms_A', ...
    'Iout_actual_avg_A', ...
    'Iout_ripple_rms_A', ...
    'Vout_ripple_pp_V', ...
    'Icomm_primary_min_abs_A', ...
    'Icomm_secondary_min_abs_A', ...
    'Icomm_all_min_abs_A', ...
    'Ecomm_min_uJ', ...
    'Vrms_primary_bridge_V', ...
    'Vrms_secondary_referred_V', ...
    'transformer_VA_primary', ...
    'transformer_VA_secondary_ref', ...
    'max_di_dt_A_per_us'});

writetable( ...
    result_table, ...
    fullfile(cfg.output_dir, ...
    'DAB_TPS_LOAD_POINTS_all_V2.csv'));

end


%==========================================================================
% Console reporting
%==========================================================================
function print_configuration(cfg)

f_resonance_Hz = ...
    1.0 / ...
    (2.0 * pi * sqrt(cfg.L_H * cfg.Cprimary_F));

XL_ohm = 2.0 * pi * cfg.fs_Hz * cfg.L_H;
XC_ohm = 1.0 / ...
    (2.0 * pi * cfg.fs_Hz * cfg.Cprimary_F);

fprintf('\n');
fprintf('============================================================\n');
fprintf(' DAB TPS FIVE-LOAD-POINT ANALYSIS V2\n');
fprintf('============================================================\n');
fprintf('VA                         = %.1f V\n', cfg.VA_V);
fprintf('VB cases                   = ');
fprintf('%.0f ', cfg.VB_cases_V);
fprintf('V\n');
fprintf('Load points                = ');
fprintf('%.0f%% ', 100.0 * cfg.load_ratio);
fprintf('\n');
fprintf('Transformer ratio          = %.3f\n', cfg.n);
fprintf('Switching frequency        = %.3f kHz\n', ...
        cfg.fs_Hz / 1000.0);
fprintf('DAB inductance             = %.3f uH\n', ...
        cfg.L_H * 1e6);
fprintf('Primary series capacitor   = %.3f uF\n', ...
        cfg.Cprimary_F * 1e6);
fprintf('Secondary series capacitor = REMOVED\n');
fprintf('Output capacitor           = %.3f uF\n', ...
        cfg.Co_F * 1e6);
fprintf('L-C resonance              = %.3f kHz\n', ...
        f_resonance_Hz / 1000.0);
fprintf('XL at fs                   = %.4f ohm\n', XL_ohm);
fprintf('XC at fs                   = %.4f ohm\n', XC_ohm);
fprintf('Net fundamental reactance  = %.4f ohm\n', ...
        XL_ohm - XC_ohm);
fprintf('TBPRD at 100-MHz TBCLK     = %u\n', cfg.TBPRD);
fprintf('============================================================\n');

end


function print_operating_point_table(result, cfg)

fprintf('\n');
fprintf(['VB  Load Pref  p     Headroom Mode ' ...
         'D1deg D2deg D3deg ILrms ILpk Vopp\n']);

fprintf(['-------------------------------------------------------------' ...
         '---------------------\n']);

for point = 1:length(result.VB_V)
    fprintf(['%3.0f %4.0f%% %4.1f %.3f  %.3f   M%u  ' ...
             '%5.1f %5.1f %5.1f %5.1f %5.1f %.3f\n'], ...
        result.VB_V(point), ...
        result.load_percent(point), ...
        result.Pref_W(point) / 1000.0, ...
        result.p(point), ...
        result.p_headroom(point), ...
        result.mode_id(point), ...
        result.D1_deg(point), ...
        result.D2_deg(point), ...
        result.D3_deg(point), ...
        result.Irms_with_Cp_A(point), ...
        result.Ipk_with_Cp_A(point), ...
        result.Vout_ripple_pp_V(point));
end

fprintf(['-------------------------------------------------------------' ...
         '---------------------\n']);

fprintf('\nPower-utilization check at 100%% load:\n');

for voltage_index = 1:length(cfg.VB_cases_V)
    VB = cfg.VB_cases_V(voltage_index);

    point = find( ...
        (abs(result.VB_V - VB) < 1e-9) & ...
        (abs(result.load_percent - 100.0) < 1e-9), ...
        1, 'first');

    fprintf(['VB=%3.0f V: Pref=%.2f kW, Pbase=%.2f kW, ' ...
             'p=%.3f, headroom=%.2f kW\n'], ...
        VB, ...
        result.Pref_W(point) / 1000.0, ...
        result.Pbase_W(point) / 1000.0, ...
        result.p(point), ...
        result.P_headroom_W(point) / 1000.0);
end

end


%==========================================================================
% Figure save helper
%==========================================================================
function save_figure(cfg, filename)

if isfield(cfg, 'save_figures') == 0
    save_enable = 1;
else
    save_enable = cfg.save_figures;
end

if save_enable ~= 0
    print(gcf, ...
          fullfile(cfg.output_dir, filename), ...
          '-dpng', '-r160');
end

end
