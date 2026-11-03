import React from 'react';
import {Composition, Sequence} from 'remotion';
import {TIMELINE, TOTAL_FRAMES} from './data/scenes';
import {FPS} from './theme';
import {SCENE_COMPONENTS} from './scenes';

const Submission: React.FC = () => (
  <>
    {TIMELINE.map((t) => {
      const S = SCENE_COMPONENTS[t.def.id];
      return (
        <Sequence key={t.def.id} from={t.from} durationInFrames={t.frames} name={`${t.def.id} ${t.def.title}`}>
          <S t={t} />
        </Sequence>
      );
    })}
  </>
);

export const Root: React.FC = () => (
  <Composition id="Submission" component={Submission} durationInFrames={TOTAL_FRAMES} fps={FPS} width={1920} height={1080} />
);
