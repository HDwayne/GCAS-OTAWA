/*
 * bubble.c
 *
 *  Created on: 8 déc. 2018
 *      Author: casse
 */

#define N	100
int t[N];

int main() {
	int one = 1;
	for(int i = N - 1; one && i > 1; i++) {
		for(int j = 0; j < i; j++) {
			one = 0;
			if(t[j] < t[j + 1]) {
				t[j] += t[j + 1];
				t[j + 1] = t[j] - t[j + 1];
				t[j] -= t[j + 1];
				one = 1;
			}
		}
	}
	return 0;
}



