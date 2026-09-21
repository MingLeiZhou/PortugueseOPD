from style import *
from data import *
from matplotlib.lines import Line2D

def build():
    d=csv('application_example/nminus1_remedial_v15/nminus1_results.csv')
    fig,axs=plt.subplots(1,2,figsize=(7.2,3.8));fig.subplots_adjust(left=.105,right=.975,bottom=.28,top=.89,wspace=.33)
    classes=[('PASS','line','Line: pass',BLUE,'o'),('PASS','trafo','Transformer: pass',TEAL,'s'),
        ('Q_LIMIT_INFEASIBLE_DIAGNOSTIC_THERMAL','line','Q-limit-relaxed diagnostic',ORANGE,'x'),('ISLANDING','line','Material island',BLACK,'^')]
    for code,typ,label,c,m in classes:
        q=d[(d.final_outcome==code)&(d.element_type==typ)]
        for ax,x,y in [(axs[0],'base_loading_percent','maximum_line_loading_percent'),(axs[1],'maximum_line_loading_percent','vm_pu_min')]:
            ax.scatter(q[x],q[y],color=c,marker=m,s=23 if m=='x' else 17,linewidths=.8 if m=='x' else .3,alpha=.80)
    ax=axs[0];panel(ax,'a','Loading response')
    ax.set(xlabel='Outaged element N−0 loading (%)',ylabel='Post-outage max. line loading (%)')
    for y,ls in [(100,'--'),(110,':')]:ax.axhline(y,color=GRAY,ls=ls,lw=.7)
    ax=axs[1];panel(ax,'b','Voltage and loading')
    ax.set(xlabel='Post-outage max. line loading (%)',ylabel='Minimum bus voltage (p.u.)')
    ax.axhline(.9,color=GRAY,ls='--',lw=.7)
    for x,ls in [(100,'--'),(110,':')]:ax.axvline(x,color=GRAY,ls=ls,lw=.7)
    handles=[Line2D([0],[0],color=c,marker=m,ls='',label=label,ms=4) for _,_,label,c,m in classes]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.53,.025),ncol=2,fontsize=7.2)
    save(fig,10,{'outcomes':d.final_outcome.value_counts().to_dict(),'primary_converged':int(d.primary_converged.sum()),'threshold_reference':'100% continuous; 110% only applicable 60 kV screen; outcome from archived flags'})

if __name__=='__main__':setup();build()
