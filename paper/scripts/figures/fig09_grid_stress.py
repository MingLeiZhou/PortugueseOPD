from style import *
from data import *

def build():
    d=csv('application_example/application_sweep.csv')
    fig,axs=plt.subplots(1,2,figsize=(7.2,3.15));fig.subplots_adjust(left=.10,right=.975,bottom=.21,top=.86,wspace=.34)
    ax=axs[0];panel(ax,'a','Maximum loading')
    for col,name,c,m in [('line_loading_percent_max','Line',BLUE,'o'),('transformer_loading_percent_max','Transformer',TEAL,'s')]:ax.plot(d.scaling,d[col],marker=m,color=c,label=name)
    ax.axhline(100,color=GRAY,ls='--',lw=.8);ax.set_ylabel('Loading (%)');ax.legend(loc='upper left');grid(ax)
    ax=axs[1];panel(ax,'b','Voltage extrema')
    for col,name,c,m in [('vm_pu_min','Minimum',BLUE,'o'),('vm_pu_max','Maximum',TEAL,'s')]:ax.plot(d.scaling,d[col],marker=m,color=c,label=name)
    for y in [.9,1.1]:ax.axhline(y,color=GRAY,ls='--',lw=.8)
    ax.set(ylabel='Voltage (p.u.)',ylim=(.89,1.11));ax.legend(loc='center right');grid(ax)
    for ax in axs:ax.set(xlabel='Input scale s',xticks=d.scaling)
    save(fig,9,{'solves':5,'intervals':'none; deterministic response connecting sampled scales'})

if __name__=='__main__':setup();build()
