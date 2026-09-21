from style import *
from data import *

def build():
    m=csv('external_evidence_validation/municipality_comparison.csv')
    s=csv('external_evidence_validation/substation_comparison.csv')
    ci=csv('validation_experiments/validation_confidence_intervals.csv')
    fig,axs=plt.subplots(1,2,figsize=(7.2,3.65));fig.subplots_adjust(left=.105,right=.97,bottom=.18,top=.88,wspace=.36)
    for ax,d,x,y,domain,l,title,xlabel,ylabel,scale in [
      (axs[0],m,'billed_share_within_matched','pt60_share_within_matched','MUNICIPAL_SPATIAL','a','Municipality-month','Billed energy share (%)','Substation energy share (%)',100),
      (axs[1],s,'reference_natural_load_mw','pt60_observed_peak_mw','SUBSTATION_SPATIAL','b','Substation-season','Public natural peak load (MW)','SimPT60 peak load (MW)',1)]:
        q=d[d.matched].dropna(subset=[x,y]);lim=max(q[x].max(),q[y].max())*scale*1.06
        ax.scatter(q[x]*scale,q[y]*scale,s=6,alpha=.40,color=BLUE,lw=0)
        ax.plot([0,lim],[0,lim],'--',c=GRAY,lw=.7)
        ax.set(xlim=(0,lim),ylim=(0,lim),xlabel=xlabel,ylabel=ylabel,aspect='equal');panel(ax,l,title)
        c=ci[(ci.domain==domain)&(ci.metric=='spearman')].iloc[0];assert len(q)==c.observations
        ax.text(.04,.96,f'n = {len(q):,}; clusters = {int(c.independent_blocks)}\nρ = {c.estimate:.3f}\n95% CI [{c.ci_lower:.3f}, {c.ci_upper:.3f}]',transform=ax.transAxes,va='top',fontsize=7.3)
    save(fig,7,{'confidence_intervals':'archived 1000-replicate cluster bootstrap; not recomputed'})

if __name__=='__main__':setup();build()
