from gpp3323.i18n import tr
"""Shared selectable plots for live load measurements and CSV review."""
import tkinter as tk
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from gui.monitor_tab import Sample, engineering_scale


def quantity_scale(values, unit):
    if unit == 'W':
        return (1000.0, 'mW') if values and max(map(abs, values)) < 1 else (1.0, 'W')
    return engineering_scale(values, unit)


def format_power(value):
    factor, unit = quantity_scale([value], 'W')
    return f'{value * factor:.5g} {unit}'


class CurvePanel(ttk.Frame):
    QUANTITIES = [('Voltage', 'voltage', 'V'), ('Current', 'current', 'A'), ('Power', 'power', 'W')]

    def __init__(self, parent, *, choose_power_source=False):
        super().__init__(parent)
        self.series: list[tuple[str, list[Sample]]] = []
        self.align_zero = False
        self.variables = {}
        self.axes = {}
        self.power_source = tk.StringVar(value=tr('計算值 (V×I)'))
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        ttk.Label(controls, text=tr('顯示曲線：')).pack(side='left')
        for title, attr, _ in self.QUANTITIES:
            variable = tk.BooleanVar(value=True)
            self.variables[attr] = variable
            ttk.Checkbutton(controls, text=title, variable=variable,
                            command=self._layout).pack(side='left', padx=5)
        if choose_power_source:
            power_controls = ttk.Frame(self)
            power_controls.pack(fill='x')
            ttk.Label(power_controls, text=tr('功率來源：')).pack(side='left')
            selector = ttk.Combobox(power_controls, textvariable=self.power_source,
                                   values=(tr('計算值 (V×I)'), tr('儀器讀回值')),
                                   state='readonly', width=23)
            selector.pack(side='left', padx=5)
            selector.bind('<<ComboboxSelected>>', lambda _event: self._layout())
        self.figure = Figure(figsize=(9, 5.5), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        self.toolbar.pack(side='bottom', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self._layout()

    def _layout(self):
        self.figure.clear()
        self.axes = {}
        selected = [q for q in self.QUANTITIES if self.variables[q[1]].get()]
        shared = None
        for index, (_, attr, _) in enumerate(selected, 1):
            axis = self.figure.add_subplot(len(selected), 1, index, sharex=shared)
            shared = shared or axis
            self.axes[attr] = axis
        if not selected:
            self.figure.text(.5, .5, 'Select Voltage / Current / Power', ha='center')
        self.toolbar.update()
        self.draw()

    def set_series(self, series, align_zero=False):
        self.series = series
        self.align_zero = align_zero
        self.draw()

    def draw(self):
        # Preserve an interactive zoom during live updates if the scale is unchanged.
        for title, attr, unit in self.QUANTITIES:
            axis = self.axes.get(attr)
            if axis is None:
                continue
            limits = (axis.get_xlim(), axis.get_ylim())
            zoomed = not axis.get_autoscalex_on() or not axis.get_autoscaley_on()
            old_label = axis.get_ylabel()
            axis.clear()
            value_attr = attr
            if attr == 'power':
                calculated = self.power_source.get() == tr('計算值 (V×I)')
                value_attr = 'calculated_power' if calculated else 'power'
                title = 'Power V×I' if calculated else 'Power readback'
            values = [getattr(s, value_attr) for _, samples in self.series for s in samples]
            factor, display_unit = quantity_scale(values, unit)
            for index, (label, samples) in enumerate(self.series):
                if not samples:
                    continue
                origin = min(s.elapsed for s in samples) if self.align_zero else 0
                axis.plot([s.elapsed - origin for s in samples],
                          [getattr(s, value_attr) * factor for s in samples],
                          label=label, color=f'C{index % 10}', linestyle=['-', '--', ':', '-.'][(index // 10) % 4])
            axis.set_ylabel(f'{title} ({display_unit})')
            axis.grid(True, alpha=.25)
            axis.ticklabel_format(axis='y', style='plain', useOffset=False)
            axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
            axis.margins(y=.08)
            if axis.lines:
                axis.legend(loc='upper right', fontsize='small')
            if zoomed and old_label == axis.get_ylabel():
                axis.set_xlim(limits[0])
                axis.set_ylim(limits[1])
        if self.axes:
            list(self.axes.values())[-1].set_xlabel('Elapsed time (s)')
        self.canvas.draw_idle()
