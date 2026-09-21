"""Abstract connectivity rules, not invented geographical bus positions."""
from style import *
from data import source,save
from matplotlib.patches import Ellipse

def build():
    source('portuguese_hv_network/config/model_config.json')
    fig,axs=plt.subplots(2,2,figsize=(7.2,4.1))
    fig.subplots_adjust(left=.04,right=.97,bottom=.05,top=.93,hspace=.31,wspace=.14)
    for ax in axs.flat:
        ax.set(xlim=(0,1),ylim=(0,1));ax.axis('off')
    ax=axs[0,0];panel(ax,'a','Same-voltage endpoint matching')
    for x,y in [(.40,.65),(.43,.72),(.47,.66)]:
        ax.plot([.03,x],[y+.10,y],color=GRAY,lw=.8);ax.plot(x,y,'o',mfc='white',mec=BLACK)
    ax.add_patch(Ellipse((.435,.68),.19,.30,fill=False,ls=':',lw=.8,ec=GRAY))
    ax.annotate('',xy=(.69,.68),xytext=(.56,.68),arrowprops={'arrowstyle':'->','lw':.6,'color':GRAY})
    ax.plot([.70,.90],[.68,.68],color=BLACK);ax.plot(.90,.68,'o',color=BLACK)
    ax.text(.43,.45,'Cluster ≤75 m',ha='center');ax.text(.90,.45,'Bus',ha='center')
    ax.plot([.08,.39],[.17,.17],color=BLACK);ax.plot(.39,.17,'o',mfc='white',mec=BLACK)
    ax.plot(.81,.17,'s',mfc='white',mec=BLACK,ms=5)
    ax.annotate('',xy=(.78,.17),xytext=(.43,.17),arrowprops={'arrowstyle':'<->','lw':.6,'color':GRAY})
    ax.text(.61,.27,'≤250 m',ha='center');ax.text(.81,.03,'Facility',ha='center')
    ax=axs[0,1];panel(ax,'b','Crossing and electrical connection')
    for x in [.25,.75]:
        ax.plot([x-.18,x+.18],[.55,.55],color=BLACK)
        if x==.25:
            ax.plot([x,x],[.28,.51],color=BLACK);ax.plot([x,x],[.59,.82],color=BLACK)
        else:
            ax.plot([x,x],[.28,.82],color=BLACK);ax.plot(x,.55,'o',color=BLACK,ms=4)
    ax.text(.25,.12,'Crossing only',ha='center');ax.text(.75,.12,'Explicit shared node',ha='center')
    ax=axs[1,0];panel(ax,'c','Circuit and segment identities')
    for x in [.12,.88]:ax.plot([x,x],[.22,.81],color=BLACK,lw=2)
    for y in [.39,.67]:ax.plot([.12,.88],[y,y],color=BLACK)
    ax.plot(.50,.67,'o',mfc='white',mec=BLACK,ms=4)
    ax.text(.30,.77,'A.1',ha='center');ax.text(.69,.77,'A.2',ha='center')
    ax.text(.50,.26,'B',ha='center');ax.text(.50,.06,'A: serial segments; B: parallel circuit',ha='center')
    ax=axs[1,1];panel(ax,'d','Transformer-mediated voltage tiers')
    for y in [.80,.49,.18]:
        ax.plot([.03,.16],[y,y],color=BLACK);ax.plot([.31,.44],[y,y],color=BLACK)
        ax.add_patch(Ellipse((.215,y),.10,.20,fill=False,lw=.85,ec=BLACK))
        ax.add_patch(Ellipse((.275,y),.10,.20,fill=False,lw=.85,ec=BLACK))
    ax.text(.50,.80,'Explicit asset: ≤1 km',va='center')
    ax.text(.50,.49,'Co-located voltage tiers',va='center')
    ax.text(.50,.18,'RARI boundary: ≤5 km',va='center')
    save(fig,1,{'type':'abstract method schematic; coordinates have no geographic meaning'})

if __name__=='__main__':setup();build()
