import { ChangeDetectionStrategy, Component } from '@angular/core';
import { SupervisorComponent } from './supervisor/supervisor.component';

@Component({
  selector: 'atl-root',
  imports: [SupervisorComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {}
