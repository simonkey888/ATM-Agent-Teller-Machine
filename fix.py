import time
from typing import Any

class AtmPhase2:
    def __init__(self, **settings):
        self.order = settings.get('order', 'ORDER-004')
        self.phase = settings.get('phase', 'PHASE-2')
        self.flag = settings.get('flag', 'HURRY_UP_TIME_IS_MONEY')
        self.engine = True

    def pump(self):
        while self.engine:
            self.phase = 'PUMP'
            # Simulate money flow
            time.sleep(0.25)

    def final_out(self):
        if not self.engine:
            print(f"{self.order} — {self.phase}")
            print(self.flag)

    def run(self):
        self.pump()
        self.final_out()

if __name__ == '__main__':
    machine = AtmPhase2(
        order='ORDER-004',
        phase='PHASE-2',
        flag='HURRY_UP_TIME_IS_MONEY'
    )
    machine.run()