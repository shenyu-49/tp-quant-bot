/**
 * TP钱包Web3连接模块
 * 支持 TP钱包 (TokenPocket) 的 Browser Provider
 */

const TP_PROVIDER = 'https://tp-token.sg.mdex.com/';

class TPWalletProvider {
    constructor() {
        this.provider = null;
        this.address = null;
        this.chainId = null;
        this.isConnected = false;
    }

    /**
     * 获取TP钱包Provider
     * TP钱包在浏览器中注入 window.tp
     */
    getTPProvider() {
        // 方式1: TP钱包原生注入
        if (window.dapp) {
            return window.dapp;
        }
        // 方式2: TokenPocket浏览器
        if (window.ethereum && window.ethereum.isTokenPocket) {
            return window.ethereum;
        }
        // 方式3: 直接注入
        if (window.tp) {
            return window.tp;
        }
        return null;
    }

    /**
     * 检查是否已安装TP钱包
     */
    isTPWalletInstalled() {
        return this.getTPProvider() !== null;
    }

    /**
     * 连接钱包
     */
    async connect() {
        try {
            this.provider = this.getTPProvider();
            
            if (!this.provider) {
                throw new Error('请安装TP钱包');
            }

            // 请求账户权限
            const accounts = await this.provider.request({
                method: 'eth_requestAccounts'
            });

            if (accounts && accounts.length > 0) {
                this.address = accounts[0];
                this.isConnected = true;
                
                // 获取链ID
                this.chainId = await this.provider.request({
                    method: 'eth_chainId'
                });

                console.log('TP钱包已连接:', this.address);
                console.log('链ID:', this.chainId);

                return {
                    success: true,
                    address: this.address,
                    chainId: this.chainId
                };
            }
            
            throw new Error('未获取到钱包账户');
        } catch (error) {
            console.error('连接失败:', error);
            return {
                success: false,
                error: error.message
            };
        }
    }

    /**
     * 断开连接
     */
    disconnect() {
        this.address = null;
        this.chainId = null;
        this.isConnected = false;
        console.log('钱包已断开');
    }

    /**
     * 获取余额
     */
    async getBalance(address = this.address) {
        if (!this.provider || !address) return 0;
        
        try {
            const balance = await this.provider.request({
                method: 'eth_getBalance',
                params: [address, 'latest']
            });
            // 转换为ETH单位
            return parseInt(balance, 16) / 1e18;
        } catch (error) {
            console.error('获取余额失败:', error);
            return 0;
        }
    }

    /**
     * 切换网络
     */
    async switchNetwork(chainId) {
        if (!this.provider) return false;
        
        try {
            await this.provider.request({
                method: 'wallet_switchEthereumChain',
                params: [{ chainId: chainId }]
            });
            return true;
        } catch (error) {
            console.error('切换网络失败:', error);
            return false;
        }
    }

    /**
     * 签名消息
     */
    async signMessage(message) {
        if (!this.provider || !this.address) {
            throw new Error('请先连接钱包');
        }

        try {
            const signature = await this.provider.request({
                method: 'personal_sign',
                params: [message, this.address]
            });
            return signature;
        } catch (error) {
            console.error('签名失败:', error);
            throw error;
        }
    }

    /**
     * 发送交易 (通过TP钱包签名)
     */
    async sendTransaction(txParams) {
        if (!this.provider || !this.address) {
            throw new Error('请先连接钱包');
        }

        try {
            const txHash = await this.provider.request({
                method: 'eth_sendTransaction',
                params: [{
                    from: this.address,
                    to: txParams.to,
                    value: txParams.value || '0x0',
                    data: txParams.data || '0x',
                    gas: txParams.gas || undefined,
                    gasPrice: txParams.gasPrice || undefined,
                }]
            });
            
            return {
                success: true,
                txHash: txHash
            };
        } catch (error) {
            console.error('交易失败:', error);
            return {
                success: false,
                error: error.message
            };
        }
    }

    /**
     * 监听账户变化
     */
    onAccountsChanged(callback) {
        if (this.provider) {
            this.provider.on('accountsChanged', (accounts) => {
                if (accounts.length === 0) {
                    this.disconnect();
                } else {
                    this.address = accounts[0];
                }
                callback(accounts);
            });
        }
    }

    /**
     * 监听链变化
     */
    onChainChanged(callback) {
        if (this.provider) {
            this.provider.on('chainChanged', (chainId) => {
                this.chainId = chainId;
                callback(chainId);
            });
        }
    }
}

// 导出单例
const tpWallet = new TPWalletProvider();
