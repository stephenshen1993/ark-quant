package com.stephenshen.arkquant.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 标的信息
 *
 * @author stephenshen
 * @date 2024/8/28 06:58:32
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class Security {
    private String symbol;
    private String name; // 标的名称
    private Double price; // 当前价格
    private Integer lotSize; // 交易单位，即每手的数量
    // private String assetType; // 证券类型 如"stock", "convertible_bond"
}
