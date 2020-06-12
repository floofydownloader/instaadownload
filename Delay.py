class Delay(object):
    def __init__(self):
        self.delaylist = {}
    
    def reset_delay(self):
        self.delaylist = {}

    def capture_delay(self, delay, priority):
        if priority not in self.delaylist:
            self.delaylist[priority] = []
        
        if len(self.delaylist[priority]) >= 20:
            self.delaylist[priority].remove(self.delaylist[priority][0])

        self.delaylist[priority].append(delay)

    def get_delay(self, priority):
        if priority not in self.delaylist:
            self.delaylist[priority] = []
        
        delay = 0
        i = 0
        for d in self.delaylist[priority]:
            delay += d
            i += 1
        
        if i > 0:
            delay = delay / i
        
        return int(delay * 10) / 10