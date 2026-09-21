from style import *
from data import *

def build():
    d=csv('annual_nminus1_panel_v2/annual_nminus1_panel.csv')
    fig=plt.figure(figsize=(7.2,8.3))
    # Top row has its own left margin, leaving sufficient label space below.
    top=fig.add_gridspec(1,2,left=.13,right=.90,bottom=.70,top=.945,wspace=.82,width_ratios=[1,.97])
    ax=fig.add_subplot(top[0,0]);panel(ax,'a','Screening criteria')
    counts=[]
    for r in ROLES:
        q=d[d.representative_role==r];a=q.passes_screen;i=q.passes_incremental_contingency_screen
        assert not (a&~i).any();counts.append([int(a.sum()),int((i&~a).sum()),int((~i).sum())])
    counts=np.array(counts);left=np.zeros(6)
    for j,label,c in zip(range(3),['Absolute pass','Incremental only','Remaining'],[BLUE,ORANGE,LIGHT]):
        ax.barh(range(6),counts[:,j],left=left,color=c,label=label,height=.65);left+=counts[:,j]
    ax.set(yticks=range(6),yticklabels=ROLE_NAMES,ylim=(5.6,-.6),xlabel='Contingencies',xlim=(0,1700),xticks=[0,800,1600])
    handles,labels=ax.get_legend_handles_labels();fig.legend(handles,labels,loc='center',bbox_to_anchor=(.51,.635),ncol=3,fontsize=7.2,columnspacing=1.3,handlelength=1.2)
    ax=fig.add_subplot(top[0,1]);panel(ax,'b','Failure indicators')
    M=np.array([[int(d[d.representative_role==r][c].sum()) for r in ROLES] for c in ['material_islanding','incremental_thermal_violation','incremental_voltage_violation','incremental_transformer_violation']]+[[int(((d.representative_role==r)&~d.primary_converged).sum()) for r in ROLES]])
    ax.imshow(M,cmap='Blues',vmin=0,vmax=M.max(),aspect='auto')
    for (i,j),v in np.ndenumerate(M):ax.text(j,i,str(v),ha='center',va='center',fontsize=6.9,color='white' if v>M.max()*.58 else BLACK)
    ax.set(yticks=range(5),yticklabels=['Island','New line','New voltage','New transformer','Primary failed'],xticks=range(6),xticklabels=['Export','Import','Solar','Wind','Min','Peak'])
    ax.tick_params(axis='x',rotation=55,labelsize=6.8);ax.tick_params(length=0)
    matrices={}
    for row,col,l,title,cmap,unit in [(1,'maximum_line_loading_percent','c','Largest post-outage line loading','YlOrBr','Loading (%)'),(2,'unsupplied_load_mw','d','Largest disconnected load','Blues','Load (MW)')]:
        ax=fig.add_axes([.32,.38 if row==1 else .085,.56,.19]);panel(ax,l,title)
        q=d[d.primary_converged].copy() if row==1 else d.copy()
        ids=q.groupby('element_id')[col].max().nlargest(5).index
        A=q[q.element_id.isin(ids)].pivot(index='element_id',columns='representative_role',values=col).reindex(index=ids,columns=ROLES)
        values=A.to_numpy();cm=plt.get_cmap(cmap).copy();cm.set_bad('#EEEEEE')
        im=ax.imshow(np.ma.masked_invalid(values),cmap=cm,vmin=0,aspect='auto')
        for (i,j),v in np.ndenumerate(values):ax.text(j,i,'NA' if not np.isfinite(v) else f'{v:.0f}',ha='center',va='center',fontsize=7.5,color='white' if np.isfinite(v) and v>np.nanmax(values)*.64 else BLACK)
        def short(e):
            return str(e).replace('EREDES:circuit:','ER: ').replace('OSM:circuit:','OSM: ').replace('OSM:relation:','OSM: ').replace('OSM:way:','OSM: ').replace('TRAFO:','T: ')
        ax.set(yticks=range(5),yticklabels=[short(e) for e in ids],xticks=range(6),xticklabels=ROLE_NAMES)
        ax.tick_params(axis='y',labelsize=6.8);ax.tick_params(length=0)
        p=ax.get_position();ca=fig.add_axes([.897,p.y0,.012,p.height]);fig.colorbar(im,cax=ca).set_label(unit)
        for s in ax.spines.values():s.set_visible(False)
        OUT.mkdir(parents=True,exist_ok=True);A.to_csv(OUT/f'fig11_panel_{l}_values.csv')
        matrices[l]={'element_ids':list(ids),'roles':ROLES,'selection':'largest per-element maximum across roles','missing_cells':int(A.isna().sum().sum())}
    save(fig,11,{'count':len(d),'primary_converged':int(d.primary_converged.sum()),'absolute_pass':int(d.passes_screen.sum()),'incremental_pass':int(d.passes_incremental_contingency_screen.sum()),'screening_counts':counts.tolist(),'failure_indicators':M.tolist(),'matrices':matrices})

if __name__=='__main__':setup();build()
