from style import *
from data import *
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D

def build():
    d=scope();ci=csv('validation_experiments/validation_confidence_intervals.csv')
    fig=plt.figure(figsize=(7.2,7.0))
    gs=fig.add_gridspec(3,2,left=.10,right=.88,top=.91,bottom=.075,hspace=.62,wspace=.34,height_ratios=[1,1.42,.90])
    hexes=[];hexaxes=[];counts={}
    for j,(typ,domain) in enumerate([('consumption','LOAD_CONSUMPTION'),('wind','WIND')]):
        a=f'ren_{typ}_mw';b='external_load_mw' if j==0 else 'external_wind_mw'
        q,p=daily_pair(d,a,b);counts[typ]=len(q)
        ax=fig.add_subplot(gs[0,j]);panel(ax,chr(97+j),['Consumption','Wind generation'][j])
        ax.plot(p.index,p[a]/1000,color=BLUE,lw=.85);ax.plot(p.index,p[b]/1000,color=ORANGE,ls='--',lw=.85)
        ax.set_ylabel('Daily mean (GW)');dates(ax);grid(ax)
        ax=fig.add_subplot(gs[1,j]);hexaxes.append(ax);panel(ax,chr(99+j),'15-minute pairs')
        lim=max(q[a].max(),q[b].max())/1000*1.025
        hb=ax.hexbin(q[b]/1000,q[a]/1000,gridsize=45,extent=(0,lim,0,lim),mincnt=1,cmap='Blues',linewidths=0)
        hexes.append(hb);ax.plot([0,lim],[0,lim],'--',color=GRAY,lw=.7)
        ax.set(xlim=(0,lim),ylim=(0,lim),xlabel='E-REDES (GW)',ylabel='REN / SimPT60 (GW)',aspect='equal')
        ax.set_anchor('W')
        r=ci[(ci.domain==domain)&(ci.metric=='pearson')].iloc[0]
        assert len(q)==r.observations
        ax.text(.04,.96,f'n = {len(q):,}\nr = {r.estimate:.4f}',transform=ax.transAxes,va='top',fontsize=7.5)
    norm=LogNorm(1,max(h.get_array().max() for h in hexes))
    for h in hexes:h.set_norm(norm)
    p=hexaxes[1].get_position();ca=fig.add_axes([.90,p.y0,.012,p.height])
    fig.colorbar(hexes[0],cax=ca).set_label('Pairs per hexagon')
    ax=fig.add_subplot(gs[2,:]);panel(ax,'e','Paired intraday consumption')
    q=d.dropna(subset=['ren_consumption_mw','external_load_mw'])
    for col,c,ls in [('ren_consumption_mw',BLUE,'-'),('external_load_mw',ORANGE,'--')]:
        p=q.groupby('minute_of_day')[col].mean();ax.plot(p.index/60,p/p.mean(),color=c,ls=ls,lw=1.1)
    ax.set(xlim=(0,24),xticks=[0,6,12,18,24],xlabel='Local hour (Europe/Lisbon)',ylabel='Mean-normalized\nconsumption');grid(ax)
    fig.legend(handles=[Line2D([0],[0],color=BLUE,label='REN / SimPT60'),Line2D([0],[0],color=ORANGE,ls='--',label='E-REDES')],loc='upper center',bbox_to_anchor=(.5,.98),ncol=2)
    save(fig,6,{'paired_counts':counts,'density_norm':'shared logarithmic count scale','no_interpolation':True})

if __name__=='__main__':setup();build()
