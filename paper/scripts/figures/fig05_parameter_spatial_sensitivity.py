from style import *
from data import *
from matplotlib.lines import Line2D

NAMES={'PARAM_IMPEDANCE_HIGH':'R/X +20%; C −20%','PARAM_IMPEDANCE_LOW':'R/X −20%; C +20%',
 'PARAM_RATING_HIGH':'Current rating +20%','PARAM_RATING_LOW':'Current rating −20%',
 'PARAM_TRANSFORMER_CONSERVATIVE':'Transformer: conservative','PARAM_TRANSFORMER_OPTIMISTIC':'Transformer: optimistic',
 'SPATIAL_BOUNDARY_UNIFORM':'Boundary: uniform','SPATIAL_BOUNDARY_VOLTAGE':'Boundary: voltage',
 'SPATIAL_GENERATION_CAPACITY':'Generation: capacity','SPATIAL_LOAD_CAPACITY':'Residual load: capacity','SPATIAL_LOAD_UNIFORM':'Residual load: uniform'}

def build():
    source('portuguese_hv_network/outputs/validation_experiments/parameter_scenarios.json')
    d=csv('validation_experiments/sensitivity_summary.csv')
    fig,axs=plt.subplots(1,3,figsize=(7.2,4.3),sharey=True);fig.subplots_adjust(left=.29,right=.975,bottom=.17,top=.87,wspace=.16)
    specs=[('line_rank_spearman_median','line_rank_spearman_min','Spearman ρ',(.955,1.002)),('top20_jaccard_median','top20_jaccard_min','Top-20 Jaccard',(.38,1.025)),('median_abs_delta_max_line_loading_pp','max_abs_delta_max_line_loading_pp','|Δ max. loading| (pp)',(0,37))]
    for j,(ax,(med,worst,label,lim)) in enumerate(zip(axs,specs)):
        panel(ax,chr(97+j))
        for i,r in d.iterrows():
            c=BLUE if r.experiment=='PARAMETER' else TEAL;m='o' if r.experiment=='PARAMETER' else 's'
            ax.plot([r[med],r[worst]],[i,i],color=c,lw=1)
            ax.plot(r[med],i,marker=m,color=c,ms=3.8);ax.plot(r[worst],i,'|',color=c,ms=7)
        ax.axhline(5.5,color=LIGHT,lw=.65);ax.set(xlabel=label,xlim=lim,yticks=range(len(d)),ylim=(10.6,-.6));grid(ax,'x')
    axs[0].set_yticklabels([NAMES[s] for s in d.variant])
    fig.legend(handles=[Line2D([0],[0],marker='o',ls='',color=BLUE,label='Parameter'),Line2D([0],[0],marker='s',ls='',color=TEAL,label='Spatial allocation'),Line2D([0],[0],marker='|',ls='',color=GRAY,label='Worst of 22 states')],loc='upper center',bbox_to_anchor=(.61,.985),ncol=3,fontsize=7)
    save(fig,5,{'summary':'dot=median; segment=median to worst; not confidence interval','unique_cases':264})

if __name__=='__main__':setup();build()
