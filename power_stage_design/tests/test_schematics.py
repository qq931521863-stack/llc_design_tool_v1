"""Circuit-net, branch-current and real Qt SVG tests for the original drawings.

These tests inspect actual terminal geometry, then use the numerical branch
currents on the drawn circuit. Device counts alone cannot establish topology.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import cache
from pathlib import Path

import numpy as np
import pytest

from power_stage_design.models import DesignInputs
from power_stage_design.schematic import _circuit, layout, svg_schematic
from power_stage_design.topologies import calculate_stage

TOPOLOGIES=tuple(f"T{i:02}" for i in range(1,20))


def _lies(point,line):
    """Independent axis-aligned point/segment predicate."""
    x,y=point;a,b,c,d=line
    return ((a==c==x and min(b,d)<=y<=max(b,d)) or
            (b==d==y and min(a,c)<=x<=max(a,c)))


def _parts(topology):
    return {e.key:e for e in layout(topology)[0]}


@cache
def _result(topology):
    if topology=="T04":
        x=DesignInputs(topology,24,5,.2,100e3,47e-6,capacitance_f=100e-6,
                       diode_drop_v=.4,leakage_inductance_h=1e-6,
                       primary_capacitance_f=47e-6,primary_output_current_a=.1)
    elif topology=="T11":
        x=DesignInputs(topology,325,12,1,100e3,1e-3,turns_ratio_np_ns=10,
                       diode_drop_v=.7,resonant_capacitance_f=100e-12)
    elif topology=="T18":
        x=DesignInputs(topology,400,48,5,100e3,100e-6,turns_ratio_np_ns=4,
                       diode_drop_v=.7,magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6)
    else:
        x=DesignInputs(topology,48,96 if topology=="T02" else 5,2,100e3,470e-6,
                       capacitance_f=1e-3,turns_ratio_np_ns=2,diode_drop_v=0 if topology=="T19" else .7,
                       secondary_inductance_h=100e-6,transfer_capacitance_f=1e-3,magnetizing_inductance_h=1e-3)
    return calculate_stage(x)


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_every_drawn_terminal_is_wired_to_exactly_its_intended_net(topology):
    c=_circuit(topology)
    for e in c.elements:
        assert len(e.nets)==len(e.terminals)
        for net,p in zip(e.nets,e.terminals):
            assert {n for n,line in c.routes if _lies(p,line)}=={net},(topology,e.key,p)
    # A wire endpoint cannot accidentally land on another net. Crossovers
    # may intersect only inside both segments, where SVG draws a bridge.
    for net,line in c.routes:
        for point in (line[:2],line[2:]):
            assert all(other==net or not _lies(point,segment) for other,segment in c.routes)


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_each_named_net_is_geometrically_connected_and_no_wire_bypasses_a_symbol(topology):
    c=_circuit(topology);points=defaultdict(set)
    for net,line in c.routes:points[net].update((line[:2],line[2:]))
    for e in c.elements:
        for net,p in zip(e.nets,e.terminals):points[net].add(p)
    for net,x,y in c.grounds:points[net].add((x,y))
    for net,ps in points.items():
        parents={p:p for p in ps}
        def root(p,parents=parents):
            while parents[p]!=p:p=parents[p]
            return p
        def join(a,b,parents=parents,root=root):parents[root(a)]=root(b)
        for name,line in c.routes:
            if name==net:
                along=[p for p in ps if _lies(p,line)]
                for p in along:join(p,along[0])
        grounds=[(x,y) for name,x,y in c.grounds if name==net]
        for p in grounds:join(p,grounds[0])
        assert len({root(p) for p in ps})==1,(topology,net)
    for e in c.elements:
        if not e.nets:continue
        dx,dy=(10,20) if e.vertical else (20,10)
        for _,(a,b,d,f) in c.routes:
            vertical=a==d and e.x-dx<a<e.x+dx and max(min(b,f),e.y-dy)<min(max(b,f),e.y+dy)
            horizontal=b==f and e.y-dy<b<e.y+dy and max(min(a,d),e.x-dx)<min(max(a,d),e.x+dx)
            assert not(vertical or horizontal),(topology,e.key,"wire crosses device body")


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_all_and_only_model_waveform_devices_have_distinct_click_targets(topology):
    parts=layout(topology)[0];selectable={e.key for e in parts if e.selectable}
    assert selectable==set(_result(topology).waveforms)
    for target in parts:
        hits=[e.key for e in parts if e.contains(target.x,target.y)]
        assert hits==([target.key] if target.selectable else []),(topology,target.key,hits)
    svg=ET.fromstring(svg_schematic(topology))
    assert {e.attrib["data-device"] for e in svg.iter() if "data-device" in e.attrib}==selectable


def test_inverting_and_two_inductor_polarities_are_actual_connections():
    p=_parts("T03")
    assert p["D1"].nets==("SW","OUT") and p["D1"].reverse
    assert p["Cout"].reverse and _circuit("T03").negative_output
    sepic,zeta,cuk=map(_parts,("T06","T07","T08"))
    assert sepic["Q1"].nets==("A","GND") and sepic["L1"].nets==("VIN","A")
    assert sepic["D1"].nets==("B","OUT") and sepic["L2"].nets==("B","GND")
    assert zeta["Q1"].nets==("VIN","A") and zeta["L1"].nets==("A","GND")
    assert zeta["Ctransfer"].reverse and zeta["D1"].reverse
    assert cuk["D1"].nets==("B","GND") and not cuk["D1"].reverse
    assert cuk["L2"].nets==("B","OUT") and cuk["Cout"].reverse


def test_reset_and_isolation_circuits_are_not_generic_transformer_boxes():
    for t in ("T09","T10","T11"):
        p=_parts(t)
        assert (p["primary"].dot,p["secondary"].dot)==(0,1)
    for t in ("T12","T13","T14"):
        p=_parts(t)
        assert (p["primary"].dot,p["secondary"].dot)==(0,0)
    p=_parts("T12")
    assert p["Cclamp"].nets==("VIN","CLAMP") and p["Cclamp"].reverse
    assert p["Q2"].nets==("CLAMP","N")
    p=_parts("T13")
    assert p["reset_winding"].nets==("RESET","GND") and p["reset_winding"].dot==1
    assert p["Dreset1"].nets==("VIN","RESET") and p["Dreset1"].reverse
    for topology,prefix in (("T10","Dclamp"),("T14","Dreset")):
        p=_parts(topology)
        assert p[prefix+"1"].nets==("VIN","N") and p[prefix+"1"].reverse
        assert p[prefix+"2"].nets==("P","GND") and p[prefix+"2"].reverse
        assert p["Q1"].nets==("VIN","P") and p["Q2"].nets==("N","GND")
    for topology in ("T04","T09","T10","T11","T12","T13","T14","T15","T16","T17","T18"):
        c=_circuit(topology)
        assert any(n=="GND" for n,_,_ in c.grounds)
        assert any(n=="ISO" for n,_,_ in c.grounds)
        assert all("GND" not in e.nets for e in c.elements if e.key.startswith("secondary"))
        assert not _parts(topology)["Lm"].nets  # state selector, no fictitious parallel copper path


def test_bridge_numbering_center_taps_and_series_capacitor_buck_feed():
    for t in ("T15","T16","T17","T18"):
        p=_parts(t)
        assert p["secondary1"].nets==("S1","ISO")
        assert p["secondary2"].nets==("ISO","S2")
        assert p["secondary1"].terminals[1]==p["secondary2"].terminals[0]
        assert p["secondary1"].dot==p["secondary2"].dot==0
        assert p["D1"].nets==("S1","RECT") and p["D2"].nets==("S2","RECT")
    p=_parts("T15")
    assert p["Csplit1"].nets==("VIN","MID") and p["Csplit2"].nets==("MID","GND")
    assert p["primary"].nets==("A","MID") and "Cin" not in p
    p=_parts("T16")
    assert p["primary1"].nets==("P1","VIN") and p["primary2"].nets==("VIN","P2")
    assert p["primary1"].dot==p["primary2"].dot==1
    assert p["primary1"].terminals[1]==p["primary2"].terminals[0]
    p=_parts("T17")
    assert p["Q1"].nets==("VIN","A") and p["Q3"].nets==("B","GND")
    assert p["Q2"].nets==("VIN","B") and p["Q4"].nets==("A","GND")
    p=_parts("T18")
    assert p["Q1"].nets==("VIN","A") and p["Q4"].nets==("B","GND")
    assert p["Q3"].nets==("VIN","B") and p["Q2"].nets==("A","GND")
    assert p["Lleak"].nets==("A","P") and p["primary"].nets==("P","B")
    p=_parts("T19")
    assert p["Q1a"].nets==("VIN","TOP")
    assert p["Cseries"].nets==("TOP","A") and p["Q1b"].nets==("TOP","B")
    assert p["Q2a"].nets==("A","GND") and p["Q2b"].nets==("B","GND")
    assert p["L1"].nets==("A","OUT") and p["L2"].nets==("B","OUT")


def _value(wave,fraction):
    for s in wave.segments:
        if s.start_fraction<=fraction<s.end_fraction:
            return float(s.evaluate((fraction-s.start_fraction)/(s.end_fraction-s.start_fraction)))
    raise AssertionError("sample outside waveform")


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_model_branch_currents_obey_kcl_at_every_drawn_net(topology):
    result=_result(topology);parts=_parts(topology)
    # Signs refer to the geometrical terminal order, not a copied KCL formula.
    reverse={"T01":{"D1"},"T02":set(),"T03":{"D1","Cout"},
             "T04":{"secondary"},"T05":{"D1"},"T06":{"L2"},
             "T07":{"D1","Ctransfer"},"T08":{"L2","Cout"},
             "T09":{"secondary"},"T10":{"secondary","Dclamp1","Dclamp2"},"T11":{"secondary"},
             "T12":{"secondary","D2","Cclamp","Q2"},
             "T13":{"secondary","D2","reset_winding","Dreset1"},
             "T14":{"secondary","D2","Dreset1","Dreset2"},
             "T15":{"secondary1"},"T16":{"primary1","secondary1"},
             "T17":{"secondary1"},"T18":{"secondary1"},"T19":{"Q2a","Q2b"}}[topology]
    # Use interior fractions of every model interval, including dead/idle,
    # reset, commutation and circulating states. A capacitor average is not
    # a replacement for its instantaneous branch waveform here.
    edges={s.start_fraction for dev in result.waveforms.values() for s in dev.current_a.segments}|{1.0}
    times=[a+(b-a)*f for a,b in zip(sorted(edges),sorted(edges)[1:]) for f in (.17,.5,.83)]
    for fraction in times:
        residual=defaultdict(float)
        for key,e in parts.items():
            if not e.nets or not e.selectable:continue
            current=_value(result.waveforms[key].current_a,fraction)*(-1 if key in reverse else 1)
            residual[e.nets[0]]+=current;residual[e.nets[1]]-=current
        if topology=="T11":
            # The drawn parameter-only capacitor has finite displacement
            # current inside intervals. Its turn-on impulse is deliberately
            # excluded; no finite switch-discharge RMS is inferred.
            v=result.waveforms["Q1"].voltage_v
            seg=next(s for s in v.segments if s.start_fraction<=fraction<s.end_fraction)
            u=(fraction-seg.start_fraction)/(seg.end_fraction-seg.start_fraction)
            a,b,w=seg.harmonic
            du=np.polynomial.polynomial.polyval(u,np.polynomial.polynomial.polyder(seg.coefficients))-a*w*np.sin(w*u)+b*w*np.cos(w*u)
            current=result.inputs.resonant_capacitance_f*du/((seg.end_fraction-seg.start_fraction)*v.period_s)
            residual["N"]+=current;residual["GND"]-=current
        load=result.inputs.iout_a*(-1 if topology in ("T03","T08") else 1)
        residual["OUT"]+=load;residual["ISO" if "ISO" in residual else "GND"]-=load
        if topology=="T04":
            residual["PRIMARY_OUT"]+=result.inputs.primary_output_current_a
            residual["GND"]-=result.inputs.primary_output_current_a
        source=result.metrics["input_current_mean_a"]
        if topology=="T15":
            # Equal split C and a stiff total bus require a pulsed source.
            # Unlike the other drawings, there is no independent Cin here
            # that absorbs the difference from the cycle-average source.
            source=.5*sum(_value(result.waveforms[key].current_a,fraction)
                          for key in ("Q1","Q2"))
        residual["VIN"]-=source;residual["GND"]+=source
        assert max(abs(v) for v in residual.values())<2e-7,(topology,fraction,dict(residual))


def test_half_bridge_source_is_pulsed_and_its_integral_is_the_reported_mean():
    result=_result("T15");p=result.inputs
    current=lambda key,t: _value(result.waveforms[key].current_a,t)
    source=lambda t: .5*(current("Q1",t)+current("Q2",t))
    # Independent original-circuit CCM KVL/volt-seconds at the red-test point.
    # No production duty, statistics or capacitor recurrence supplies the oracle.
    half_period=.5/p.fsw_hz;primary_pulse=p.vin_v/2
    secondary_pulse=primary_pulse/p.turns_ratio_np_ns
    duty=(p.vout_v+p.diode_drop_v)/secondary_pulse
    slope=(secondary_pulse-p.vout_v-p.diode_drop_v)/p.inductance_h
    ripple=slope*duty*half_period
    phase=.040375;time=phase/p.fsw_hz
    il=p.iout_a-ripple/2+slope*time
    im=-primary_pulse*duty*half_period/(2*p.magnetizing_inductance_h)+primary_pulse*time/p.magnetizing_inductance_h
    independent_source=(il/p.turns_ratio_np_ns+im)/2
    assert independent_source==pytest.approx(.4879686037234042,abs=1e-14)
    assert source(phase)==pytest.approx(independent_source,abs=1e-13)
    assert source(.35)==pytest.approx(0.,abs=1e-14)
    mean=(p.vout_v+p.diode_drop_v)*p.iout_a/p.vin_v
    assert mean==pytest.approx(.2375,abs=1e-14)
    # A fabricated constant waveform can pass an integral check, not these two
    # point checks. Keep the exact old residual as the negative control.
    assert source(phase)-mean==pytest.approx(.2504686037234042,abs=1e-13)
    nodes,weights=np.polynomial.legendre.leggauss(5)
    edges=sorted({s.start_fraction for key in ("Q1","Q2")
                  for s in result.waveforms[key].current_a.segments}|{1.})
    integral=sum(w*(b-a)/2*source(a+(node+1)*(b-a)/2)
                 for a,b in zip(edges,edges[1:]) for node,w in zip(nodes,weights))
    assert integral==pytest.approx(mean,abs=1e-13)
    assert result.metrics["input_current_mean_a"]==pytest.approx(integral,abs=1e-13)
    for t in (phase,.35,.5+phase,.85):
        assert source(t)==pytest.approx(current("Q1",t)+current("Csplit1",t),abs=1e-13)
        assert source(t)==pytest.approx(current("Q2",t)+current("Csplit2",t),abs=1e-13)


def test_llc_reuses_its_workspace_and_unknown_topology_gets_no_fake_circuit():
    for topology in ("T20","T21","invalid"):
        assert layout(topology)==([],[])
        svg=svg_schematic(topology)
        assert "data-device" not in svg
        assert "LLC workspace" in svg if topology!="invalid" else "Unknown topology" in svg


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_real_qt_svg_renders_each_device_and_selection_without_changing_circuit(topology,tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    qt=pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app=qt.QApplication.instance() or qt.QApplication([])
    def render(selected=None):
        r=QSvgRenderer(QByteArray(svg_schematic(topology,selected=selected).encode()))
        assert r.isValid() and r.viewBoxF()==QRectF(0,0,740,285)
        im=QImage(1480,570,QImage.Format.Format_ARGB32);im.fill(0xffffffff)
        painter=QPainter(im);r.render(painter,QRectF(0,0,1480,570));painter.end()
        return im
    font=QFont("sans-serif");font.setPixelSize(12);metrics=QFontMetricsF(font)
    labels=[]
    for e in layout(topology)[0]:
        extent=metrics.horizontalAdvance(e.label);x=e.x+e.label_dx;y=e.y+e.label_dy
        x-=extent if e.label_dx<0 else extent/2 if e.label_dx==0 else 0
        box=QRectF(x,y-metrics.ascent(),extent,metrics.height())
        assert box.left()>=0 and box.right()<=740 and box.top()>=0 and box.bottom()<270
        assert all(not box.intersects(other) for other in labels),(topology,e.key,"overlapping component labels")
        labels.append(box)
        for _,(a,b,d,f) in _circuit(topology).routes:
            vertical=a==d and box.left()<a<box.right() and max(min(b,f),box.top())<min(max(b,f),box.bottom())
            horizontal=b==f and box.top()<b<box.bottom() and max(min(a,d),box.left())<min(max(a,d),box.right())
            assert not(vertical or horizontal),(topology,e.key,"label obscures an electrical wire")
    plain=render();evidence=os.environ.get("PSD_SCHEMATIC_EVIDENCE_DIR")
    if evidence:
        directory=Path(evidence);directory.mkdir(parents=True,exist_ok=True)
        assert plain.save(str(directory/f"{topology}.png"))
        (directory/f"{topology}.svg").write_text(svg_schematic(topology),encoding="utf-8")
    for e in layout(topology)[0]:
        if not e.selectable:continue
        selected=render(e.key)
        # Selection really changes pixels around this component. Every
        # unselected outside region remains bit-identical to the baseline.
        area=selected.copy(int((e.x-12)*2),int((e.y-12)*2),48,48)
        assert area!=plain.copy(int((e.x-12)*2),int((e.y-12)*2),48,48)
        assert selected.copy(0,0,100,50)==plain.copy(0,0,100,50)
    app.processEvents()


@pytest.mark.parametrize("topology,keys", [("T01", None), ("T18", {"Coss1"})])
@pytest.mark.parametrize("size", [(1280, 235), (1560, 400), (520, 500)])
def test_widget_fits_without_stretching_and_clicks_only_in_circuit(topology, keys, size):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from power_stage_design.gui import SchematicView
    app = qt.QApplication.instance() or qt.QApplication([])
    view = SchematicView()
    view.topology_id = topology
    view.device_keys = keys
    view.selected = None
    view.refresh()
    view.resize(*size)
    view.show()
    app.processEvents()
    circuit = _circuit(topology, device_keys=keys)
    scale = min(view.width()/circuit.width, view.height()/circuit.height)
    left = (view.width()-scale*circuit.width)/2
    top = (view.height()-scale*circuit.height)/2
    # Independently measure the rendered input-source circle: its four axes
    # have the same radius, even when the widget has a very different ratio.
    image = view.grab().toImage()
    for x, y in ((13, 145), (57, 145), (35, 123), (35, 167)):
        px, py = round(left+x*scale), round(top+y*scale)
        assert min(image.pixelColor(px+dx, py+dy).lightness()
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)) < 220
    selected = []
    view.component_selected.connect(selected.append)
    for part in circuit.elements:
        before = len(selected)
        QTest.mouseClick(view, Qt.MouseButton.LeftButton,
                        pos=QPoint(round(left+scale*part.x), round(top+scale*part.y)))
        assert selected[before:] == ([part.key] if part.selectable else [])
    before = len(selected)
    for point in (QPoint(1, 1), QPoint(view.width()-2, view.height()-2)):
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert len(selected) == before
    view.close()


@pytest.mark.parametrize("topology",TOPOLOGIES)
def test_real_widget_mouse_events_select_the_drawn_device_at_scaled_size(topology):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    qt=pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
    from PySide6.QtTest import QTest

    from power_stage_design.gui import SchematicView
    app=qt.QApplication.instance() or qt.QApplication([])
    view=SchematicView();view.topology_id=topology;view.refresh()
    view.resize(1036,399);view.show();app.processEvents()
    events=[];view.component_selected.connect(events.append)
    try:
        for e in layout(topology)[0]:
            before=len(events)
            point=QPoint(round(e.x*view.width()/740),round(e.y*view.height()/285))
            QTest.mouseClick(view,Qt.MouseButton.LeftButton,pos=point)
            app.processEvents()
            assert events[before:]==([e.key] if e.selectable else [])
        evidence=os.environ.get("PSD_SCHEMATIC_EVIDENCE_DIR")
        if evidence:assert view.grab().save(str(Path(evidence)/f"{topology}-widget.png"))
    finally:
        view.close();view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


@pytest.mark.parametrize("topology,keys", [(t, None) for t in TOPOLOGIES] + [
    ("T05", {"Coss1"}), ("T11", {"Cnode", "Dbody"}), ("T18", {"Coss1"}),
])
def test_selection_does_not_erase_actual_copper_at_component_ports(topology, keys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app = qt.QApplication.instance() or qt.QApplication([])
    circuit = _circuit(topology, device_keys=keys)

    def render(selected=None):
        image = QImage(2*circuit.width, 2*circuit.height, QImage.Format.Format_ARGB32)
        image.fill(0xffffffff)
        painter = QPainter(image)
        QSvgRenderer(QByteArray(svg_schematic(topology, selected=selected, device_keys=keys).encode())).render(
            painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
        return image

    plain = render()
    for part in circuit.elements:
        if not part.selectable:
            continue
        selected = render(part.key)
        for terminal in part.terminals:
            for _, route in circuit.routes:
                if not _lies(terminal, route):
                    continue
                # The last 1–3 drawing units of external copper used to be
                # covered by the selection fill, visually disconnecting parts.
                x, y = terminal
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    for distance in (1, 2, 3):
                        point = (x+dx*distance, y+dy*distance)
                        if _lies(point, route):
                            px, py = (round(2*v) for v in point)
                            if plain.pixelColor(px, py).lightness() < 150:
                                assert selected.pixelColor(px, py).lightness() < 150, (topology, part.key, point)
    app.processEvents()


@pytest.mark.parametrize("topology,keys", [
    ("T05", {"Coss1"}), ("T11", {"Cnode", "Dbody"}), ("T18", {"Coss1"}),
])
def test_dynamic_labels_clear_real_rendered_wires_and_glyphs(topology, keys):
    """Use Qt's actual SVG font/glyph rasterization, not guessed text widths."""
    import copy
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app = qt.QApplication.instance() or qt.QApplication([])
    circuit = _circuit(topology, device_keys=keys)
    root = ET.fromstring(svg_schematic(topology, device_keys=keys))

    def alpha(svg):
        image = QImage(3*circuit.width, 3*circuit.height, QImage.Format.Format_RGBA8888)
        image.fill(0)
        painter = QPainter(image)
        QSvgRenderer(QByteArray(ET.tostring(svg))).render(
            painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
        return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
            image.height(), image.width(), 4)[:, :, 3].copy()

    obstacles = copy.deepcopy(root)
    for child in list(obstacles):
        if child.tag.endswith("text") or child.tag.endswith("rect"):
            obstacles.remove(child)
    solid = alpha(obstacles)
    labels = [child for child in root if child.tag.endswith("text")]
    for label in labels:
        only = ET.Element(root.tag, root.attrib)
        only.append(copy.deepcopy(label))
        pixels = alpha(only)
        assert not np.any((pixels > 64) & (solid > 64)), (topology, label.text)

    # Prove that the original positions collide with actual copper/glyphs.
    if topology in ("T11", "T18"):
        bad = copy.deepcopy(next(label for label in labels
                                 if label.text == ("Cnode" if topology == "T11" else "VB probe")))
        bad.attrib.update({"x": "193", "y": "209", "text-anchor": "end"} if topology == "T11"
                          else {"x": "607", "y": "240", "text-anchor": "middle"})
        only = ET.Element(root.tag, root.attrib)
        only.append(bad)
        assert np.any((alpha(only) > 64) & (solid > 64))
    app.processEvents()
