"""Render the README evidence image from the committed public feed."""
import json,textwrap
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'data/sbengaged-events.json').read_text(encoding='utf8'))
events=sorted(data['events'],key=lambda e:e['score'],reverse=True)[:8]
fig,ax=plt.subplots(figsize=(12,7),layout='constrained')
fig.set_facecolor('#f4edda');ax.set_facecolor('#f4edda')
ax.barh(range(len(events)),[e['score'] for e in events],color='#934b2b')
ax.set_yticks(range(len(events)),['\n'.join(textwrap.wrap(e['title'],38)) for e in events])
ax.invert_yaxis();ax.set_xlabel('Model probability × food-quality weight')
ax.set_title('Freefood Radar SBU',loc='left',fontsize=24,pad=44,color='#173b2c')
ax.text(0,1.03,'Public SB Engaged daily feed · '+data['fetched_at'][:10]+' · model rankings',transform=ax.transAxes,color='#684f3c')
ax.spines[['top','right']].set_visible(False)
fig.savefig(ROOT/'docs/results.png',dpi=150)
