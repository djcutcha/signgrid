import React from 'react';
import {TItem} from '../data/timeline';
import {S01} from './S01'; import {S02} from './S02'; import {S03} from './S03'; import {S04} from './S04'; import {S04b} from './S04b';
import {S05} from './S05'; import {S06} from './S06'; import {S07} from './S07'; import {S08} from './S08'; import {S09} from './S09'; import {S10} from './S10';

export const SCENES: Record<string, React.FC<{t: TItem}>> = {
  '01': S01, '02': S02, '03': S03, '04': S04, '04b': S04b, '05': S05, '06': S06, '07': S07, '08': S08, '09': S09, '10': S10,
};
