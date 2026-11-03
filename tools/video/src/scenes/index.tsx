import React from 'react';
import {TItem} from '../data/timeline';
import {S01} from './S01'; import {S02} from './S02'; import {S03} from './S03'; import {S04} from './S04';
import {S05} from './S05'; import {S06a} from './S06a'; import {S06b} from './S06b'; import {S07} from './S07';
import {S08} from './S08'; import {S09} from './S09'; import {S10} from './S10';

export const SCENES: Record<string, React.FC<{t: TItem}>> = {
  '01': S01, '02': S02, '03': S03, '04': S04, '05': S05, '06a': S06a, '06b': S06b, '07': S07, '08': S08, '09': S09, '10': S10,
};
