from style import *
from data import *
from matplotlib.lines import Line2D

def build():
    d=scope();fig=plt.figure(figsize=(7.2,4.95))
    gs=fig.add_gridspec(2,2,left=.10,right=.975,top=.86,bottom=.115,hspace=.62,wspace=.35)
    for j,typ in enumerate(['solar','hydro']):
        ax=fig.add_subplot(gs[0,j]);panel(ax,chr(97+j),typ.title())
        a=f'ren_{typ}_mw';b=f'external_{typ}_mw';q,p=daily_pair(d,a,b)
        ax.plot(p.index,p[a]/1000,color=BLUE,lw=.9);ax.plot(p.index,p[b]/1000,color=ORANGE,ls='--',lw=.9)
        ax.set_ylabel('Paired daily mean (GW)');dates(ax);grid(ax)
    fig.legend(handles=[Line2D([0],[0],color=BLUE,label='REN national generation'),Line2D([0],[0],color=ORANGE,ls='--',label='E-REDES distribution injection')],loc='upper center',bbox_to_anchor=(.5,.98),ncol=2)
    ax=fig.add_subplot(gs[1,:]);panel(ax,'c','Absolute balance residual distributions')
    stats={}
    for col,name,c,ls in [('model_ac_balance_closure_mw','Model AC closure',TEAL,'-'),('ren_input_balance_residual_mw','REN input residual',BLUE,'--'),('external_balance_gap_mw','External accounting gap',ORANGE,':')]:
        v=np.sort(d[col].dropna().abs().to_numpy());positive=v>0
        # The y positions retain zero observations in the denominator.
        ax.step(v[positive],(np.arange(len(v))+1)[positive]/len(v),where='post',color=c,ls=ls,label=name)
        stats[col]={'n':len(v),'exact_zero_count':int((v==0).sum()),'mean_absolute_mw':float(v.mean())}
    ax.set(xscale='log',ylim=(0,1.025),xlabel='Absolute residual (MW)',ylabel='Empirical cumulative\nprobability');grid(ax)
    ax.legend(loc='upper left',bbox_to_anchor=(.025,.83),fontsize=7)
    save(fig,8,{'ecdf':stats,'zero_handling':'exact zeros remain in denominator; no epsilon or fabricated coordinates'})

if __name__=='__main__':setup();build()
