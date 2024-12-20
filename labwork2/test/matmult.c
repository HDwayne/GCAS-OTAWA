/*
 * matmult.c
 *
 *  Created on: 8 déc. 2018
 *      Author: casse
 */

#define N	10
typedef int mat_t[N][N];
mat_t A, B, C;


int main() {
	for(int i = 0; i < N; i++) {
		for(int j = 0; j < N; j++) {
			int s = 0;
			for(int k = 0; k < N; k++)
				s += A[i][k] * B[k][j];
			if(s >= 0)
				C[i][j] = s;
			else
				C[i][j] = 0;
		}
	}
	return 0;
}
