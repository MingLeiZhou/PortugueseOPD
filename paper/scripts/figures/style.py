"""Shared publication style; one physical width for all final figures."""
import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/pt60-final-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

BLUE = '#254E70'
TEAL = '#467F78'
ORANGE = '#B77D45'
GRAY = '#777777'
LIGHT = '#D5D8DA'
BLACK = '#262626'
VC = {60:TEAL, 130:ORANGE, 150:GRAY, 220:BLUE, 400:BLACK}

def setup():
    plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':8,
        'axes.titlesize':8.5, 'axes.titleweight':'normal', 'axes.labelsize':8,
        'xtick.labelsize':7.2, 'ytick.labelsize':7.2, 'legend.fontsize':7.2,
        'axes.spines.top':False, 'axes.spines.right':False, 'axes.linewidth':.6,
        'axes.axisbelow':True, 'lines.linewidth':1, 'lines.markersize':3.5,
        'legend.frameon':False, 'legend.handlelength':2, 'legend.borderaxespad':.3,
        'xtick.major.width':.6, 'ytick.major.width':.6, 'xtick.major.size':3,
        'ytick.major.size':3, 'pdf.fonttype':42, 'ps.fonttype':42,
        'svg.fonttype':'none', 'figure.facecolor':'white', 'savefig.facecolor':'white'})

def panel(ax, letter, title=''):
    ax.set_title(f'({letter})' + (f' {title}' if title else ''),loc='left',pad=7)

def grid(ax,axis='y'):
    ax.grid(axis=axis,color='#E8E8E8',linewidth=.45)

def dates(ax):
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax.tick_params(axis='x',labelrotation=25)
