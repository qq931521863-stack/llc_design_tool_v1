"""Original, electrically connected vector schematics for the PSD models.

The drawing is a circuit netlist as well as geometry: ``Element.nets`` names
its terminals, in their geometric order. Transformer dots identify magnetic
polarity; the selectable core is the model's primary-referred Lm state, not
an extra physical winding. Split windings are genuine center taps.

Reference circuits were independently redrawn from TI SLYU036A chapters
3–18, SNVA790A figure 2-1 and SLVA750A figure 1. No TI graphic is embedded.
LLC drawings stay in the existing LLC workspace, which owns their model.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from html import escape


@dataclass(frozen=True)
class Element:
    key: str
    label: str
    x: float
    y: float
    kind: str = "box"
    vertical: bool = False
    nets: tuple[str, ...] = ()
    reverse: bool = False
    half_length: float = 22
    dot: int | None = None
    label_dx: float = 0
    label_dy: float = -25
    selectable: bool = True

    @property
    def terminals(self) -> tuple[tuple[float, float], ...]:
        if not self.nets:
            return ()
        h = self.half_length
        return ((self.x, self.y-h), (self.x, self.y+h)) if self.vertical else ((self.x-h, self.y), (self.x+h, self.y))

    def contains(self, x: float, y: float) -> bool:
        """Distinct GUI hit targets, including adjacent transformer windings."""
        if not self.selectable:
            return False
        if self.kind == "core":
            return (abs(x-self.x) <= self.half_length and abs(y-self.y) <= 13) if self.vertical else (abs(x-self.x) <= 13 and abs(y-self.y) <= self.half_length)
        wide, high = (18, self.half_length+4) if self.vertical else (self.half_length+4, 18)
        return abs(x-self.x) <= wide and abs(y-self.y) <= high


@dataclass
class _Circuit:
    elements: list[Element] = field(default_factory=list)
    # Keeping the electrical net on wires permits a crossing to remain a
    # crossing; only an actual junction gets a filled dot.
    routes: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)
    grounds: list[tuple[str, float, float]] = field(default_factory=list)
    note: str = "Ideal switches; select a component for the model's voltage/current references."
    negative_output: bool = False

    def part(self, key, x, y, kind, nets=(), *, vertical=False, reverse=False,
             half_length=22, dot=None, label=None, label_dx=None, label_dy=None, selectable=True):
        if label_dx is None:
            label_dx = 24 if vertical else 0
        if label_dy is None:
            label_dy = 4 if vertical else -23
        e = Element(key, label or key, x, y, kind, vertical, tuple(nets), reverse,
                    half_length, dot, label_dx, label_dy, selectable)
        self.elements.append(e)
        return e

    def wire(self, net, *points):
        for (x1,y1),(x2,y2) in itertools.pairwise(points):
            if (x1,y1) != (x2,y2):
                if x1 != x2 and y1 != y2:
                    raise ValueError("Schematic wires must be orthogonal")
                self.routes.append((net,(x1,y1,x2,y2)))

    def ground(self, net, x, y):
        self.grounds.append((net,x,y))

    def input(self, end=280, *, capacitor=True):
        self.wire("VIN", (35,55),(end,55))
        self.wire("GND", (35,235),(end,235))
        if capacitor:
            self.part("Cin",95,145,"capacitor",("VIN","GND"),vertical=True)
            self.wire("VIN",(95,55),(95,123))
            self.wire("GND",(95,167),(95,235))
        self.ground("GND",35,235)

    def output(self, top=55, bottom=235, *, x=600, right=690, net="OUT", ground="GND", negative=False, cap_label_left=True):
        self.negative_output = negative
        self.part("Cout",x,155,"capacitor",(net,ground),vertical=True,reverse=negative,label_dx=-24 if cap_label_left else 24)
        self.part("load",right,155,"resistor",(net,ground),vertical=True,label="Rload",selectable=False,label_dx=-20,label_dy=45)
        self.wire(net,(x,top),(right,top),(right,133))
        self.wire(net,(x,top),(x,133))
        self.wire(ground,(x,177),(x,bottom),(right,bottom),(right,177))
        self.ground(ground,x,bottom)

    def winding(self, key,x,y,nets, *, dot=0, half_length=30, label=None, left=False):
        return self.part(key,x,y,"winding",nets,vertical=True,dot=dot,half_length=half_length,
                         label=label,label_dx=-17 if left else 17,label_dy=3)

    def core(self,x,y,half_length=40):
        self.part("Lm",x,y,"core",half_length=half_length,label="Lm",label_dy=half_length+18)


def _basic(topology):
    c=_Circuit(); c.input(end=173)
    negative=topology=="T03"
    c.output(x=565,negative=negative)
    first={"T01":("Q1","switch"),"T02":("L1","inductor"),"T03":("Q1","switch")}[topology]
    c.part(first[0],195,55,first[1],("VIN","SW"))
    if topology=="T01":
        c.part("D1",280,145,"diode",("SW","GND"),vertical=True,reverse=True)
        c.part("L1",410,55,"inductor",("SW","OUT"))
    elif topology=="T02":
        c.part("Q1",280,145,"switch",("SW","GND"),vertical=True)
        c.part("D1",410,55,"diode",("SW","OUT"))
    else:
        c.part("L1",280,145,"inductor",("SW","GND"),vertical=True)
        c.part("D1",410,55,"diode",("SW","OUT"),reverse=True)
        c.note="Inverting output: Cout positive plate is at GND; D1 conducts from OUT to SW."
    c.wire("SW",(217,55),(388,55));c.wire("SW",(280,55),(280,123))
    c.wire("GND",(280,167),(280,235),(565,235));c.wire("GND",(173,235),(280,235))
    c.wire("OUT",(432,55),(565,55))
    return c


def _two_switch_buck_boost():
    c=_Circuit();c.input(end=158);c.output(x=600)
    c.part("Q1",180,55,"switch",("VIN","A"))
    c.part("D1",260,145,"diode",("A","GND"),vertical=True,reverse=True)
    c.part("L1",355,55,"inductor",("A","B"))
    c.part("Q2",450,145,"switch",("B","GND"),vertical=True)
    c.part("D2",525,55,"diode",("B","OUT"))
    c.wire("A",(202,55),(333,55));c.wire("A",(260,55),(260,123))
    c.wire("B",(377,55),(503,55));c.wire("B",(450,55),(450,123))
    c.wire("OUT",(547,55),(600,55))
    c.wire("GND",(158,235),(600,235));c.wire("GND",(260,167),(260,235));c.wire("GND",(450,167),(450,235))
    c.note="Two FETs switch together; D1 + D2 conduct in the off interval. No four-switch substitution."
    return c


def _two_inductor(topology):
    c=_Circuit();c.input(end=148);c.output(x=600,negative=topology=="T08")
    zeta=topology=="T07";cuk=topology=="T08"
    c.part("Q1" if zeta else "L1",170,55,"switch" if zeta else "inductor",("VIN","A"))
    c.part("L1" if zeta else "Q1",250,145,"inductor" if zeta else "switch",("A","GND"),vertical=True)
    c.part("Ctransfer",355,55,"capacitor",("A","B"),reverse=zeta)
    c.part("D1" if zeta or cuk else "L2",440,145,"diode" if zeta or cuk else "inductor",("B","GND"),vertical=True,reverse=zeta)
    c.part("L2" if zeta or cuk else "D1",515,55,"inductor" if zeta or cuk else "diode",("B","OUT"))
    c.wire("A",(192,55),(333,55));c.wire("A",(250,55),(250,123))
    c.wire("B",(377,55),(493,55));c.wire("B",(440,55),(440,123))
    c.wire("OUT",(537,55),(600,55))
    c.wire("GND",(148,235),(600,235));c.wire("GND",(250,167),(250,235));c.wire("GND",(440,167),(440,235))
    c.note={"T06":"SEPIC: two uncoupled inductors; transfer capacitor positive on the input side.",
            "T07":"ZETA: high-side switch; transfer capacitor positive on the output side; positive output.",
            "T08":"Ćuk: negative output; D1 anode at B, cathode at GND; Cout positive at GND."}[topology]
    return c


def _single_transformer(topology):
    """Flyback and forward variants, including real reset/clamp circuits."""
    c=_Circuit();c.input(end=280)
    flyback=topology in ("T09","T10","T11")
    dual=topology in ("T10","T14")
    y=145 if dual else 100 if topology=="T13" else 115
    pt,pb=y-30,y+30
    c.winding("primary",280,y,("P" if dual else "VIN","N"),dot=0,label="Np",left=True)
    c.winding("secondary",370,y,("SEC","ISO"),dot=1 if flyback else 0,label="Ns")
    c.core(325,140,85) if topology=="T13" else c.core(325,y)
    if dual:
        c.part("Q1",280,82,"switch",("VIN","P"),vertical=True,label_dx=23)
        c.part("Q2",280,210,"switch",("N","GND"),vertical=True,label_dx=-22)
        c.wire("VIN",(280,55),(280,60));c.wire("P",(280,104),(280,pt))
        c.wire("N",(280,pb),(280,188));c.wire("GND",(280,232),(280,235))
        first="Dclamp1" if flyback else "Dreset1";second="Dclamp2" if flyback else "Dreset2"
        c.part(first,160,100,"diode",("VIN","N"),vertical=True,reverse=True,label_dx=23)
        c.part(second,215,205,"diode",("P","GND"),vertical=True,reverse=True,label_dx=-22)
        c.wire("VIN",(160,55),(160,78));c.wire("N",(160,122),(160,pb),(280,pb))
        c.wire("P",(280,pt),(215,pt),(215,183));c.wire("GND",(215,227),(215,235))
    else:
        c.wire("VIN",(280,55),(280,pt))
        qx=230 if topology=="T13" else 280
        c.part("Q1",qx,205,"switch",("N","GND"),vertical=True)
        c.wire("N",(280,pb),(qx,pb),(qx,183));c.wire("GND",(qx,227),(qx,235))
        if topology=="T11":
            c.part("Cnode_parameter",215,205,"capacitor",("N","GND"),vertical=True,label="Cnode*",label_dx=-22,selectable=False)
            c.wire("N",(280,175),(215,175),(215,183));c.wire("GND",(215,227),(215,235))
        if topology=="T12":
            c.part("Cclamp",175,85,"capacitor",("VIN","CLAMP"),vertical=True,reverse=True,label_dx=-23)
            c.part("Q2",175,145,"switch",("CLAMP","N"),vertical=True,label_dx=23)
            c.wire("VIN",(175,55),(175,63));c.wire("CLAMP",(175,107),(175,123))
            c.wire("N",(175,167),(175,175),(280,175))
        elif topology=="T13":
            c.winding("reset_winding",280,180,("RESET","GND"),dot=1,label="Nr",left=True)
            c.part("Dreset1",170,85,"diode",("VIN","RESET"),vertical=True,reverse=True,label_dx=-22)
            c.wire("VIN",(170,55),(170,63));c.wire("RESET",(170,107),(170,150),(280,150))
            c.wire("GND",(280,210),(280,235))
    c.part("D1",445,pt,"diode",("SEC","OUT" if flyback else "RECT"))
    c.wire("SEC",(370,pt),(423,pt))
    c.wire("ISO",(370,pb),(370,235),(620,235))
    c.output(top=pt,x=620,ground="ISO")
    if flyback:
        c.wire("OUT",(467,pt),(620,pt))
    else:
        c.part("D2",495,175,"diode",("RECT","ISO"),vertical=True,reverse=True)
        c.part("L1",550,pt,"inductor",("RECT","OUT"))
        c.wire("RECT",(467,pt),(528,pt));c.wire("RECT",(495,pt),(495,153))
        c.wire("ISO",(495,197),(495,235));c.wire("OUT",(572,pt),(620,pt))
    c.note={"T09":"Flyback: opposing winding dots; Lm is a referred state, distinct from actual winding currents.",
            "T10":"Two-switch flyback: both input clamp paths shown; opposite secondary polarity.",
            "T11":"QR: Cnode* sets valley timing; positive-voltage turn-on has an impulse with no finite RMS; first zero-clamp turn-on retains signed current.",
            "T12":"Active clamp: Cclamp + Q2 across primary; capacitor positive at Q2. ZVS is not implied.",
            "T13":"Forward: opposite-polarity reset winding Nr returns magnetizing energy through Dreset1.",
            "T14":"Two-switch forward: Dreset1 + Dreset2 return magnetizing energy to the input."}[topology]
    return c


def _bridge(topology):
    c=_Circuit();half=topology=="T15";push=topology=="T16";psfb=topology=="T18"
    c.input(end=270,capacitor=not half)
    if push:
        px,sx,cy=295,395,125
        c.winding("primary1",px,100,("P1","VIN"),dot=1,half_length=25,label="Np1",left=True)
        c.winding("primary2",px,150,("VIN","P2"),dot=1,half_length=25,label="Np2",left=True)
        c.wire("VIN",(270,55),(315,55),(315,125),(px,125))
        c.part("Q1",200,205,"switch",("P1","GND"),vertical=True)
        c.part("Q2",295,205,"switch",("P2","GND"),vertical=True,label_dx=-22)
        c.wire("P1",(px,75),(200,75),(200,183));c.wire("P2",(px,175),(295,183))
        c.wire("GND",(200,227),(200,235));c.wire("GND",(295,227),(295,235),(270,235))
        c.core(345,cy,65)
    elif half:
        px,sx,cy=360,440,155
        c.part("Q1",180,90,"switch",("VIN","A"),vertical=True)
        c.part("Q2",180,200,"switch",("A","GND"),vertical=True)
        c.part("Csplit1",270,90,"capacitor",("VIN","MID"),vertical=True,label_dx=20)
        c.part("Csplit2",270,200,"capacitor",("MID","GND"),vertical=True,label_dx=20)
        c.wire("VIN",(180,55),(180,68));c.wire("VIN",(270,55),(270,68))
        c.wire("A",(180,112),(180,178));c.wire("A",(180,145),(210,145),(210,125),(px,125))
        c.wire("MID",(270,112),(270,178));c.wire("MID",(270,145),(320,145),(320,185),(px,185))
        c.wire("GND",(180,222),(180,235));c.wire("GND",(270,222),(270,235))
        c.winding("primary",px,cy,("A","MID"),label="Np",left=True)
        c.core(400,cy,65)
    else:
        px,sx,cy=(365,445,145) if psfb else (345,425,145)
        # PWM full bridge groups Q1/Q3 and Q2/Q4 diagonally; PSFB names
        # devices by leg. Keep the symbols tied to those actual model IDs.
        names=("Q1","Q2","Q3","Q4") if psfb else ("Q1","Q4","Q2","Q3")
        for key,x,y,nets in ((names[0],155,85,("VIN","A")),(names[1],155,205,("A","GND")),
                             (names[2],250,85,("VIN","B")),(names[3],250,205,("B","GND"))):
            c.part(key,x,y,"switch",nets,vertical=True,label_dx=-22 if x==155 else 22)
        for x in (155,250):
            c.wire("VIN",(x,55),(x,63));c.wire("GND",(x,227),(x,235))
        c.wire("A",(155,107),(155,183));c.wire("B",(250,107),(250,183))
        c.wire("B",(250,145),(310,145),(310,175),(px,175))
        if psfb:
            c.part("Lleak",300,115,"inductor",("A","P"),label_dy=-42)
            c.wire("A",(155,145),(195,145),(195,115),(278,115));c.wire("P",(322,115),(px,115))
        else:
            c.wire("A",(155,145),(195,145),(195,115),(px,115))
        c.winding("primary",px,cy,("P" if psfb else "A","B"),label="Np",left=True)
        c.core(px+40,cy,65)
    # Two real half-secondary windings sharing one electrical center tap.
    # Dots at the upper end of each half give opposite outer-to-tap voltage.
    c.winding("secondary1",sx,cy-25,("S1","ISO"),dot=0,half_length=25,label="Ns1")
    c.winding("secondary2",sx,cy+25,("ISO","S2"),dot=0,half_length=25,label="Ns2")
    dx=sx+65;rect=dx+43;lx=rect+45;outx=lx+50
    c.part("D1",dx,cy-50,"diode",("S1","RECT"))
    c.part("D2",dx,cy+50,"diode",("S2","RECT"),label_dy=25)
    c.wire("S1",(sx,cy-50),(dx-22,cy-50));c.wire("S2",(sx,cy+50),(dx-22,cy+50))
    c.wire("RECT",(dx+22,cy-50),(rect,cy-50),(rect,cy+50),(dx+22,cy+50))
    c.part("L1",lx,cy-50,"inductor",("RECT","OUT"))
    c.wire("RECT",(rect,cy-50),(lx-22,cy-50));c.wire("OUT",(lx+22,cy-50),(outx,cy-50))
    c.output(top=cy-50,x=outx,right=710,ground="ISO")
    c.wire("ISO",(sx,cy),(sx+27,cy),(sx+27,cy+4));c.ground("ISO",sx+27,cy+4)
    c.note={"T15":"Half bridge: split input capacitors; primary ±Vin/2; n uses one secondary half.",
            "T16":"Push-pull: center taps on both sides; Np and Ns each denote one half winding.",
            "T17":"PWM full bridge: Q1/Q3 and Q2/Q4 are diagonal pairs; center-tapped secondary.",
            "T18":"PSFB: Q1/Q4 and Q2/Q3 transfer power; Lleak is series primary leakage; ZVS unverified."}[topology]
    return c


def _flybuck():
    c=_Circuit();c.input(end=180)
    c.part("Q1",180,90,"switch",("VIN","SW"),vertical=True)
    c.part("Q2",180,200,"switch",("SW","GND"),vertical=True)
    c.wire("VIN",(180,55),(180,68));c.wire("SW",(180,112),(180,178));c.wire("GND",(180,222),(180,235))
    # Rotated coupled inductor: primary left dot, secondary right dot.
    c.part("primary",340,165,"winding",("SW","PRIMARY_OUT"),half_length=30,dot=0,label="Np",label_dy=24)
    c.part("secondary",340,85,"winding",("SEC","ISO"),half_length=30,dot=1,label="Ns",label_dy=27)
    c.part("Lm",340,125,"core",vertical=True,half_length=38,label="Lm",label_dx=-55,label_dy=4)
    c.wire("SW",(180,145),(250,145),(250,165),(310,165))
    c.part("Cprimary",450,205,"capacitor",("PRIMARY_OUT","GND"),vertical=True,label_dx=-23)
    c.wire("PRIMARY_OUT",(370,165),(450,165),(450,183));c.wire("GND",(450,227),(450,235),(180,235))
    c.part("load_primary",535,205,"current_source",("PRIMARY_OUT","GND"),vertical=True,label="Io1",label_dx=20,selectable=False)
    c.wire("PRIMARY_OUT",(450,165),(535,165),(535,183));c.wire("GND",(535,227),(535,235),(450,235))
    c.part("Lleak",425,55,"inductor",("SEC","SEC_L"))
    c.part("D1",515,55,"diode",("SEC_L","OUT"))
    c.wire("SEC",(310,85),(280,85),(280,55),(403,55));c.wire("SEC_L",(447,55),(493,55))
    c.wire("ISO",(370,85),(390,85),(390,115),(600,115),(690,115))
    # The isolated output occupies the upper lane, separate from primary Cout.
    c.part("Cout",600,85,"capacitor",("OUT","ISO"),vertical=True)
    c.part("load",690,85,"current_source",("OUT","ISO"),vertical=True,label="Io2",label_dx=-20,label_dy=45,selectable=False)
    c.wire("OUT",(537,55),(690,55),(690,63));c.wire("OUT",(600,55),(600,63))
    c.wire("ISO",(600,107),(600,115));c.wire("ISO",(690,107),(690,115));c.ground("ISO",690,115)
    c.note="Fly-Buck: synchronous primary allows reverse current; isolated secondary has series leakage."
    return c


def _series_buck():
    c=_Circuit();c.input(end=168);c.output(x=600,cap_label_left=False)
    c.part("Q1a",190,55,"switch",("VIN","TOP"))
    c.part("Cseries",320,55,"capacitor",("TOP","A"))
    c.part("L1",475,55,"inductor",("A","OUT"))
    c.part("Q2a",400,115,"switch",("A","GND"),vertical=True)
    c.part("Q1b",320,165,"switch",("TOP","B"))
    c.part("L2",475,165,"inductor",("B","OUT"))
    c.part("Q2b",400,205,"switch",("B","GND"),vertical=True)
    c.wire("TOP",(212,55),(298,55));c.wire("TOP",(250,55),(250,165),(298,165))
    c.wire("A",(342,55),(453,55));c.wire("A",(400,55),(400,93))
    c.wire("GND",(400,137),(400,145));c.ground("GND",400,145)
    c.wire("B",(342,165),(453,165));c.wire("B",(400,165),(400,183))
    c.wire("GND",(400,227),(400,235));c.wire("GND",(168,235),(600,235))
    c.wire("OUT",(497,55),(600,55));c.wire("OUT",(497,165),(550,165),(550,55))
    c.note="180° interleave: Q1b is fed BEFORE Cseries; Q2a carries both phases during phase B on-time."
    return c


def _circuit(topology_id: str, *, device_keys=None) -> _Circuit:
    topology=topology_id.upper()
    if topology in ("T01","T02","T03"):
        return _basic(topology)
    if topology=="T04":
        return _flybuck()
    if topology=="T05":
        return _two_switch_buck_boost()
    if topology in ("T06","T07","T08"):
        return _two_inductor(topology)
    if topology in ("T09","T10","T11","T12","T13","T14"):
        circuit = _single_transformer(topology)
        if topology == "T11" and device_keys is not None and "Cnode" in device_keys:
            circuit.elements = [replace(e, key="Cnode", label="Cnode", selectable=True)
                                if e.key == "Cnode_parameter" else e for e in circuit.elements]
            circuit.note = "QR first zero clamp: Cnode voltage/current are finite; signed channel and displacement currents are distinct."
        return circuit
    if topology in ("T15","T16","T17","T18"):
        return _bridge(topology)
    if topology=="T19":
        return _series_buck()
    return _Circuit()


def layout(topology_id: str, *, device_keys=None) -> tuple[list[Element], list[tuple[float, ...]]]:
    """Return modeled device hit targets and electrical wire segments."""
    circuit=_circuit(topology_id,device_keys=device_keys)
    return circuit.elements,[line for _,line in circuit.routes]


def _on_segment(point, line):
    x,y=point;x1,y1,x2,y2=line
    return min(x1,x2)<=x<=max(x1,x2) and min(y1,y2)<=y<=max(y1,y2) and (x1==x2==x or y1==y2==y)


def _junctions(circuit):
    candidates={(line[0],line[1]) for _,line in circuit.routes}|{(line[2],line[3]) for _,line in circuit.routes}
    candidates.update(p for e in circuit.elements for p in e.terminals)
    result=set()
    for point in candidates:
        directions={}
        x,y=point
        for net,line in circuit.routes:
            if _on_segment(point,line):
                rays=directions.setdefault(net,set())
                for a,b in ((line[0],line[1]),(line[2],line[3])):
                    if a<x:rays.add("L")
                    if a>x:rays.add("R")
                    if b<y:rays.add("U")
                    if b>y:rays.add("D")
        if any(len(rays)>=3 for rays in directions.values()):result.add(point)
    return result


def _symbol(e):
    h=e.half_length
    if e.kind=="core":
        return f'<path d="M-4 {-h}V{h} M4 {-h}V{h}" fill="none"/>'
    lead=f'M{-h} 0H-16 M16 0H{h}'
    if e.kind=="capacitor":
        # Polarity is explicit even for the ideal, non-electrolytic model.
        return f'<path d="M{-h} 0H-5 M5 0H{h} M-5-13V13 M5-13V13" fill="none"/><text x="-15" y="-16" stroke="none" fill="currentColor" font-size="10">+</text>'
    if e.kind=="diode":
        return f'<path d="M{-h} 0H-12 M12 0H{h} M12-12V12" fill="none"/><path d="M-12-12L12 0L-12 12Z"/>'
    if e.kind=="current_source":
        return f'<path d="M{-h} 0H-14 M14 0H{h}" fill="none"/><circle cx="0" cy="0" r="14"/><path d="M-8 0H8 M3-4L8 0L3 4" fill="none"/>'
    if e.kind=="switch":
        return f'<path d="{lead} M-16 0L13-12" fill="none"/><circle cx="-16" cy="0" r="2"/><circle cx="16" cy="0" r="2"/>'
    if e.kind in ("inductor","winding"):
        # Horizontal winding; SVG rotation handles vertical windings.
        side=14 if e.kind=="winding" and e.key.startswith("secondary") else -14
        out=f'<path d="M{-h} 0H-20 c0 {side} 10 {side} 10 0 c0 {side} 10 {side} 10 0 c0 {side} 10 {side} 10 0 c0 {side} 10 {side} 10 0 H{h}" fill="none"/>'
        if e.dot is not None:
            out+=f'<circle cx="{-h+4 if e.dot==0 else h-4}" cy="{8 if side>0 else -8}" r="3" fill="currentColor" stroke="none"/>'
        return out
    return f'<path d="{lead}" fill="none"/><path d="M-16 0l4-8 8 16 8-16 8 16 4-8" fill="none"/>'


def svg_schematic(topology_id: str, *, selected: str | None = None, width: int = 740, device_keys=None) -> str:
    c=_circuit(topology_id,device_keys=device_keys)
    width=int(width)
    if width<=0:raise ValueError("SVG width must be positive")
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 285" width="{width}" height="{width*285/740:g}" role="img" aria-label="{escape(topology_id)} power circuit">',
         '<rect width="740" height="285" fill="#f7fafc"/>']
    if not c.elements:
        message="Open the existing LLC workspace for its model-linked resonant circuit." if topology_id.upper() in ("T20","T21") else "Unknown topology; no circuit has been substituted."
        return ''.join(out)+f'<text x="25" y="80" font-family="sans-serif" font-size="16">{escape(message)}</text></svg>'
    out+=['<g stroke="#334155" stroke-width="1.8" fill="none">']
    out += [f'<line x1="{a}" y1="{b}" x2="{d}" y2="{e}" data-net="{escape(net)}"/>' for net,(a,b,d,e) in c.routes]
    # Distinct nets that cross get an explicit bridge, never a junction dot.
    crossings=set()
    for n1,(x1,y1,x2,y2) in c.routes:
        if y1!=y2:continue
        for n2,(a,b,d,e) in c.routes:
            if n1!=n2 and a==d and min(x1,x2)<a<max(x1,x2) and min(b,e)<y1<max(b,e):
                crossings.add((a,y1))
    for x,y in crossings:
        out += [f'<path d="M{x-5} {y}H{x+5}" stroke="#f7fafc" stroke-width="5"/>',
                f'<path d="M{x-5} {y}a5 5 0 0 1 10 0"/>']
    out += [f'<circle cx="{x}" cy="{y}" r="2.6" fill="#334155" stroke="none"/>' for x,y in sorted(_junctions(c))]
    out+=['<circle cx="35" cy="145" r="22"/><path d="M35 55V123 M35 167V235 M28 137H42 M35 130V144 M28 155H42"/>']
    for net,x,y in c.grounds:
        if net=="ISO":
            out += [f'<path d="M{x} {y}v4 m-8 0h16 l-8 8Z"/>']
        else:
            out += [f'<path d="M{x} {y}v4 m-9 0h18 m-14 4h10 m-7 4h4"/>']
    out+=['</g>']
    for net,x,y in c.grounds:
        out += [f'<text x="{x+13}" y="{y+12}" font-size="9" fill="#64748b" font-family="sans-serif">{escape(net)}</text>']
    for e in c.elements:
        color="#0284c7" if e.key==selected else "#334155"
        transform=f'translate({e.x} {e.y})'+(' rotate(90)' if e.vertical else '')
        if e.reverse:transform+=' rotate(180)'
        attrs=f' id="component-{escape(e.key)}" data-nets="{escape(" ".join(e.nets))}"'
        if e.selectable:attrs+=f' data-device="{escape(e.key)}"'
        out += [f'<g{attrs} color="{color}" stroke="{color}" stroke-width="1.8" fill="#ffffff">',
                f'<title>{escape(e.key)}: {escape(" / ".join(e.nets) if e.nets else "primary-referred magnetizing state")}</title>']
        if e.key==selected:
            w,h=(34,2*e.half_length+8) if e.vertical else (2*e.half_length+8,34)
            if e.kind=="core":w,h=(2*e.half_length+8,24) if e.vertical else (24,2*e.half_length+8)
            out += [f'<rect x="{e.x-w/2}" y="{e.y-h/2}" width="{w}" height="{h}" rx="5" fill="#e0f2fe" stroke="none"/>']
        out += [f'<g transform="{transform}">',_symbol(e),'</g></g>']
        anchor="end" if e.label_dx<0 else "start" if e.label_dx>0 else "middle"
        out += [f'<text x="{e.x+e.label_dx}" y="{e.y+e.label_dy}" text-anchor="{anchor}" fill="{color}" font-size="12" font-family="sans-serif">{escape(e.label)}</text>']
    out += [f'<text x="12" y="20" font-family="sans-serif" font-size="12" fill="#334155">{escape(topology_id.upper())} · Vin +</text>',
            f'<text x="704" y="35" text-anchor="end" font-family="sans-serif" font-size="12" fill="#334155">Vout {"−" if c.negative_output else "+"}</text>',
            f'<text x="370" y="277" text-anchor="middle" font-family="sans-serif" font-size="10.5" fill="#64748b">{escape(c.note)}</text>','</svg>']
    return ''.join(out)
