from style import *
from data import *

def build():
    d=scope();fig=plt.figure(figsize=(7.2,5.0))
    gs=fig.add_gridspec(2,2,left=.09,right=.97,top=.87,bottom=.16,hspace=.65,wspace=.30,height_ratios=[.9,1.1])
    ax=fig.add_subplot(gs[0,:]);panel(ax,'a','Daily interval availability')
    days=pd.date_range(d.day.min(),d.day.max(),freq='D')
    starts=days.tz_localize('Europe/Lisbon').tz_convert('UTC')
    ends=(days+pd.Timedelta(days=1)).tz_localize('Europe/Lisbon').tz_convert('UTC')
    expected=pd.Series((ends-starts).total_seconds()/900,index=days)
    for name,cols,c,ls in [('Cases',[],BLUE,'-'),('Paired consumption',['ren_consumption_mw','external_load_mw'],ORANGE,'--'),('Paired wind',['ren_wind_mw','external_wind_mw'],TEAL,':')]:
        q=d.dropna(subset=cols);count=q.groupby('day').size().reindex(days,fill_value=0)
        ax.plot(days,count/expected*100,label=name,color=c,ls=ls,lw=1)
    ax.set(ylabel='Available (%)',ylim=(-3,106));dates(ax);grid(ax)
    handles,labels=ax.get_legend_handles_labels();fig.legend(handles,labels,ncol=3,loc='upper center',bbox_to_anchor=(.55,.985))
    ax=fig.add_subplot(gs[1,0]);panel(ax,'b','Week containing peak load')
    peak=d.loc[d.observed_load_mw.idxmax(),'time'];start=(peak-pd.Timedelta(days=peak.weekday())).normalize()
    q=d[(d.time>=start)&(d.time<start+pd.Timedelta(days=7))]
    for col,label,c,ls in [('observed_load_mw','Load',BLUE,'-'),('observed_generation_total_mw','Generation',TEAL,'--'),('observed_net_import_mw','Net import',ORANGE,'-')]:
        ax.plot(q.time,q[col]/1000,color=c,label=label,ls=ls,lw=.85)
    ax.axhline(0,color=GRAY,lw=.5);ax.set_ylabel('Power (GW)');grid(ax)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d',tz='Europe/Lisbon'));ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.legend(ncol=3,fontsize=6.7,loc='upper left',columnspacing=.8,handlelength=1.4)
    ax.set_ylim(top=q[['observed_load_mw','observed_generation_total_mw']].max().max()/1000*1.18)
    ax=fig.add_subplot(gs[1,1]);panel(ax,'c','Monthly completed cases')
    m=d.groupby('source_month').agg(n=('case_id','size'),converged=('converged','sum'))
    assert m.n.sum()==31492 and (m.n==m.converged).all()
    ax.bar(range(len(m)),m.n,color=BLUE,width=.67)
    ax.set(xticks=range(len(m)),xticklabels=[str(x)[2:] for x in m.index],ylabel='15-minute cases',ylim=(0,3300))
    ax.tick_params(axis='x',rotation=60);grid(ax)
    save(fig,3,{'case_count':len(d),'local_daily_denominators':sorted(expected.unique().tolist()),'no_interpolation':True})

if __name__=='__main__':setup();build()
