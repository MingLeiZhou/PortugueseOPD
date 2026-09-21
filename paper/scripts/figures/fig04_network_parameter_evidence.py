from style import *
from data import *
from matplotlib.colors import ListedColormap,BoundaryNorm
from matplotlib.patches import Patch

def build():
    d=csv('validation_experiments/topology_coverage_by_voltage.csv')
    p=csv('validation_experiments/parameter_evidence_by_voltage.csv')
    fig,axs=plt.subplots(1,2,figsize=(7.2,3.35));fig.subplots_adjust(left=.12,right=.975,bottom=.27,top=.88,wspace=.45)
    ax=axs[0];panel(ax,'a','Route length / public context')
    for i,r in d.iterrows():
        ax.plot(r.model_to_reference_ratio,i,marker='o' if r.voltage_kv<150 else 's',color=BLUE if r.voltage_kv>=150 else GRAY,ms=5)
    ax.axvline(1,color=GRAY,lw=.75,ls='--')
    ax.set(yticks=range(5),yticklabels=[f'{v} kV' + (' (n=7)' if v==130 else '') for v in d.voltage_kv],ylim=(4.5,-.5),xlim=(.70,1.17),xlabel='Length ratio');grid(ax,'x')
    A=np.zeros((5,4))
    for i,v in enumerate(d.voltage_kv):
        for j,f in enumerate(['r_status','x_status','c_status','max_i_status']):
            q=p[(p.voltage_kv==v)&(p.field==f)]
            supported=q[q.status.str.startswith(('DIRECT_','CONDUCTOR_DERIVED_'))]['count'].sum()
            A[i,j]=supported/int(d.loc[d.voltage_kv==v,'lines'].iloc[0])*100
    ax=axs[1];panel(ax,'b','Evidence type by parameter field')
    classes=np.where(A>0,2,0);classes[:,0]=np.where(A[:,0]>0,1,0)
    ax.imshow(classes,cmap=ListedColormap(['#F3F3F3',ORANGE,TEAL]),vmin=0,vmax=2,aspect='auto')
    for (i,j),v in np.ndenumerate(A):ax.text(j,i,f'{v:.1f}%',ha='center',va='center',color='white' if v>=10 else BLACK,fontsize=8)
    ax.set(xticks=range(4),xticklabels=['R','X','C','$I_{max}$'],yticks=range(5),yticklabels=[f'{v} kV' for v in d.voltage_kv])
    for s in ax.spines.values():s.set_visible(False)
    ax.tick_params(length=0);ax.set_xlabel('Parameter field')
    fig.legend(handles=[Patch(color=ORANGE,label='Project transfer (engineering assumption)'),Patch(color=TEAL,label='Source-backed rating')],loc='lower center',ncol=1,bbox_to_anchor=(.54,.005),fontsize=7)
    save(fig,4,{'evidence_share_percent':A.tolist(),'support_definition':'R: project-transfer engineering assumption; Imax: direct/source-backed rating; no direct R/X/C observations','reviewed_evidence_overlay':'paper/revision_v3/resistance_evidence_overlay.csv','130kv_caveat':'n=7; descriptive only'})

if __name__=='__main__':setup();build()
